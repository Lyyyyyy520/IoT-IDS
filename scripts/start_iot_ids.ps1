[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$BackendDir = Join-Path $ProjectRoot 'backend'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$RequirementsFile = Join-Path $BackendDir 'requirements.txt'
$PackageJsonFile = Join-Path $FrontendDir 'package.json'
$PackageLockFile = Join-Path $FrontendDir 'package-lock.json'
$VenvDir = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ViteCommand = Join-Path $FrontendDir 'node_modules\.bin\vite.cmd'
$InitMarker = Join-Path $ProjectRoot '.iot-ids-initialized.json'
$FrontendUrl = 'http://127.0.0.1:3000'
$BackendHealthUrl = 'http://127.0.0.1:5000/api/health'

function Write-Step {
    param([string]$Message)
    Write-Host "[STEP] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[ OK ] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Gray
}

function Get-HashOrEmpty {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return ''
    }

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Info $Description
    $exitCode = 0
    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

function Get-NpmCommand {
    $command = Get-Command 'npm.cmd' -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command 'npm' -ErrorAction SilentlyContinue
    }
    if (-not $command) {
        throw 'npm was not found. Install Node.js 18 or newer, then run start.bat again.'
    }

    return $command.Source
}

function Assert-NodeVersion {
    $nodeCommand = Get-Command 'node.exe' -ErrorAction SilentlyContinue
    if (-not $nodeCommand) {
        $nodeCommand = Get-Command 'node' -ErrorAction SilentlyContinue
    }
    if (-not $nodeCommand) {
        throw 'Node.js was not found. Install Node.js 18 or newer.'
    }

    $rawVersion = (& $nodeCommand.Source --version).Trim().TrimStart('v')
    try {
        $version = [version]$rawVersion
    }
    catch {
        throw "Cannot parse Node.js version: $rawVersion"
    }

    if ($version.Major -lt 18) {
        throw "Node.js $rawVersion is too old. Node.js 18 or newer is required."
    }

    Write-Ok "Node.js $rawVersion"
}

function Test-PythonCandidate {
    param(
        [string]$Command,
        [string[]]$PrefixArguments
    )

    try {
        $versionText = (& $Command @PrefixArguments -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $versionText) {
            return $null
        }

        $version = [version]$versionText
        if ($version.Major -eq 3 -and $version.Minor -ge 9 -and $version.Minor -le 13) {
            return [pscustomobject]@{
                Command = $Command
                PrefixArguments = $PrefixArguments
                Version = $versionText
            }
        }
    }
    catch {
        return $null
    }

    return $null
}

function Find-CompatiblePython {
    $pyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($selector in @('-3.12', '-3.11', '-3.10', '-3.9', '-3.13', '-3')) {
            $candidate = Test-PythonCandidate -Command $pyLauncher.Source -PrefixArguments @($selector)
            if ($candidate) {
                return $candidate
            }
        }
    }

    foreach ($name in @('python.exe', 'python3.exe', 'python', 'python3')) {
        $pythonCommand = Get-Command $name -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $candidate = Test-PythonCandidate -Command $pythonCommand.Source -PrefixArguments @()
            if ($candidate) {
                return $candidate
            }
        }
    }

    throw 'Python 3.9-3.13 was not found. Python 3.12 is recommended.'
}

function Test-PythonRuntimeReady {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        return $false
    }

    try {
        & $VenvPython -c "import flask, flask_cors, torch, onnxruntime, numpy, sklearn, pandas, scapy, yaml, openpyxl" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Test-BackendReady {
    try {
        $response = Invoke-RestMethod -Uri $BackendHealthUrl -Method Get -TimeoutSec 2
        return ($response.status -eq 'ok')
    }
    catch {
        return $false
    }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400)
    }
    catch {
        return $false
    }
}

function Test-PortListening {
    param([int]$Port)

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $asyncResult = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        $connected = $asyncResult.AsyncWaitHandle.WaitOne(500, $false)
        if (-not $connected) {
            return $false
        }

        $client.EndConnect($asyncResult)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-UntilReady {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$TestAction,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$ServiceName
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $TestAction) {
            Write-Ok "$ServiceName is ready."
            return
        }

        Start-Sleep -Seconds 1
    }

    throw "$ServiceName did not become ready within $TimeoutSeconds seconds. Check its console window."
}

try {
    Write-Host ''
    Write-Step 'Checking project files...'

    foreach ($requiredPath in @($BackendDir, $FrontendDir, $RequirementsFile, $PackageJsonFile)) {
        if (-not (Test-Path -LiteralPath $requiredPath)) {
            throw "Required project path is missing: $requiredPath"
        }
    }

    Write-Ok 'Project files are present.'

    Assert-NodeVersion
    $npmCommand = Get-NpmCommand

    $requirementsHash = Get-HashOrEmpty $RequirementsFile
    if (Test-Path -LiteralPath $PackageLockFile) {
        $frontendDependencyFile = $PackageLockFile
    }
    else {
        $frontendDependencyFile = $PackageJsonFile
    }
    $frontendDependencyHash = Get-HashOrEmpty $frontendDependencyFile

    $environmentReady = (Test-PythonRuntimeReady) -and (Test-Path -LiteralPath $ViteCommand)
    $markerMatches = $false

    if (Test-Path -LiteralPath $InitMarker) {
        try {
            $marker = Get-Content -LiteralPath $InitMarker -Raw | ConvertFrom-Json
            $markerMatches = (
                $marker.schema_version -eq 2 -and
                $marker.requirements_sha256 -eq $requirementsHash -and
                $marker.frontend_dependencies_sha256 -eq $frontendDependencyHash
            )
        }
        catch {
            $markerMatches = $false
        }
    }

    $initialized = $environmentReady -and $markerMatches

    Write-Step 'Checking initialization state...'
    if ($initialized) {
        Write-Ok 'Already initialized. Dependency installation was skipped.'
    }
    else {
        Write-Host '[INIT] Environment is missing, incomplete, or out of date.' -ForegroundColor Yellow

        $venvUsable = $false
        if (Test-Path -LiteralPath $VenvPython) {
            try {
                & $VenvPython -c "import sys; print(sys.version)" 2>$null | Out-Null
                $venvUsable = ($LASTEXITCODE -eq 0)
            }
            catch {
                $venvUsable = $false
            }
        }

        if (-not $venvUsable) {
            if (Test-Path -LiteralPath $VenvDir) {
                Write-Info 'Removing an invalid .venv directory...'
                Remove-Item -LiteralPath $VenvDir -Recurse -Force
            }

            Write-Step 'Creating Python virtual environment...'
            $python = Find-CompatiblePython
            Write-Ok "Using Python $($python.Version)"
            Invoke-CheckedCommand -FilePath $python.Command `
                -ArgumentList ($python.PrefixArguments + @('-m', 'venv', $VenvDir)) `
                -WorkingDirectory $ProjectRoot `
                -Description 'Create .venv'
        }
        else {
            Write-Info 'Existing .venv found. Dependencies will be verified and updated.'
        }

        Write-Step 'Installing backend dependencies...'
        Invoke-CheckedCommand -FilePath $VenvPython `
            -ArgumentList @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel') `
            -WorkingDirectory $ProjectRoot `
            -Description 'Upgrade pip, setuptools, and wheel'

        Invoke-CheckedCommand -FilePath $VenvPython `
            -ArgumentList @('-m', 'pip', 'install', '-r', $RequirementsFile) `
            -WorkingDirectory $ProjectRoot `
            -Description 'Install backend requirements'

        Write-Step 'Installing frontend dependencies...'
        Invoke-CheckedCommand -FilePath $npmCommand `
            -ArgumentList @('install') `
            -WorkingDirectory $FrontendDir `
            -Description 'Run npm install'

        if (-not (Test-Path -LiteralPath $ViteCommand)) {
            throw 'npm install completed, but Vite was not found in frontend/node_modules/.bin.'
        }

        if (-not (Test-PythonRuntimeReady)) {
            throw 'Backend dependency verification failed after installation.'
        }

        if (Test-Path -LiteralPath $PackageLockFile) {
            $frontendDependencyFile = $PackageLockFile
        }
        else {
            $frontendDependencyFile = $PackageJsonFile
        }

        $requirementsHash = Get-HashOrEmpty $RequirementsFile
        $frontendDependencyHash = Get-HashOrEmpty $frontendDependencyFile

        $markerData = [ordered]@{
            schema_version = 2
            initialized_at = (Get-Date).ToString('s')
            requirements_sha256 = $requirementsHash
            frontend_dependencies_sha256 = $frontendDependencyHash
        }

        $markerData | ConvertTo-Json | Set-Content -LiteralPath $InitMarker -Encoding UTF8
        Write-Ok 'Initialization completed.'
    }

    Write-Step 'Starting backend...'
    if (Test-BackendReady) {
        Write-Info 'Backend is already running.'
    }
    else {
        if (Test-PortListening -Port 5000) {
            throw 'Port 5000 is already in use by another process.'
        }

        $backendCommand = 'title IoT-IDS Backend && "' + $VenvPython + '" app.py'
        Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $backendCommand) -WorkingDirectory $BackendDir | Out-Null
    }

    Write-Step 'Starting frontend...'
    if (Test-FrontendReady) {
        Write-Info 'Frontend is already running.'
    }
    else {
        if (Test-PortListening -Port 3000) {
            throw 'Port 3000 is already in use by another process.'
        }

        $frontendCommand = 'title IoT-IDS Frontend && "' + $npmCommand + '" run dev -- --host 127.0.0.1 --port 3000'
        Start-Process -FilePath 'cmd.exe' -ArgumentList @('/k', $frontendCommand) -WorkingDirectory $FrontendDir | Out-Null
    }

    Write-Step 'Waiting for services...'
    Wait-UntilReady -TestAction { Test-BackendReady } -TimeoutSeconds 120 -ServiceName 'Backend'
    Wait-UntilReady -TestAction { Test-FrontendReady } -TimeoutSeconds 120 -ServiceName 'Frontend'

    Write-Step 'Opening browser...'
    Start-Process $FrontendUrl

    Write-Host ''
    Write-Host 'IoT-IDS is running at http://127.0.0.1:3000' -ForegroundColor Green
    Write-Host 'Keep the Backend and Frontend windows open. Close them to stop the services.' -ForegroundColor Gray
    exit 0
}
catch {
    Write-Host ''
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No successful initialization marker is written when setup fails.' -ForegroundColor Yellow
    exit 1
}

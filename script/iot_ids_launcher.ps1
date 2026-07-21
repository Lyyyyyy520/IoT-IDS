param(
    [switch]$ForceInit
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$VenvDir = Join-Path $Root '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$RuntimeDir = Join-Path $Root '.runtime'
$StateFile = Join-Path $RuntimeDir 'dependency-state.json'
$BackendPidFile = Join-Path $RuntimeDir 'backend.pid'
$FrontendPidFile = Join-Path $RuntimeDir 'frontend.pid'
$BackendUrl = 'http://127.0.0.1:5000/api/health'
$FrontendUrl = 'http://127.0.0.1:3000/'

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

function Get-FileHashValue {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Get-DependencyState {
    return [ordered]@{
        requirements = (Get-FileHashValue -Path (Join-Path $BackendDir 'requirements.txt'))
        packageLock  = (Get-FileHashValue -Path (Join-Path $FrontendDir 'package-lock.json'))
        packageJson  = (Get-FileHashValue -Path (Join-Path $FrontendDir 'package.json'))
    }
}

function Test-DependencyState {
    if (-not (Test-Path -LiteralPath $StateFile)) { return $false }
    try {
        $Saved = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
        $Current = Get-DependencyState
        return ($Saved.requirements -eq $Current.requirements -and
                $Saved.packageLock -eq $Current.packageLock -and
                $Saved.packageJson -eq $Current.packageJson)
    }
    catch {
        return $false
    }
}

function Save-DependencyState {
    $State = Get-DependencyState
    $State | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Find-PythonCommand {
    $Candidates = @(
        @{ File = 'py.exe'; Args = @('-3.12') },
        @{ File = 'py.exe'; Args = @('-3.11') },
        @{ File = 'py.exe'; Args = @('-3.10') },
        @{ File = 'py.exe'; Args = @('-3.9') },
        @{ File = 'py.exe'; Args = @('-3.13') },
        @{ File = 'python.exe'; Args = @() },
        @{ File = 'python3.exe'; Args = @() }
    )

    foreach ($Candidate in $Candidates) {
        $Command = Get-Command $Candidate.File -ErrorAction SilentlyContinue
        if ($null -eq $Command) { continue }

        try {
            $VersionArgs = @($Candidate.Args) + @('-c', "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            $VersionText = & $Command.Source $VersionArgs 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $VersionText) { continue }
            $Version = [version]($VersionText | Select-Object -First 1)
            if ($Version -ge [version]'3.9') {
                return @{
                    File = $Command.Source
                    Args = @($Candidate.Args)
                    Version = $Version.ToString()
                }
            }
        }
        catch {
            continue
        }
    }

    throw 'Python 3.9 or newer was not found. Install Python and enable the Python launcher or add Python to PATH.'
}

function Get-NpmCommand {
    $NodeCommand = Get-Command 'node.exe' -ErrorAction SilentlyContinue
    if ($null -eq $NodeCommand) {
        throw 'Node.js was not found. Install Node.js 18 or newer and add it to PATH.'
    }

    $NodeVersionText = & $NodeCommand.Source --version
    if ($LASTEXITCODE -ne 0 -or -not $NodeVersionText) {
        throw 'Unable to read the Node.js version.'
    }

    $NodeMajor = [int](($NodeVersionText.TrimStart('v') -split '\.')[0])
    if ($NodeMajor -lt 18) {
        throw "Node.js 18 or newer is required. Current version: $NodeVersionText"
    }

    $NpmCommand = Get-Command 'npm.cmd' -ErrorAction SilentlyContinue
    if ($null -eq $NpmCommand) {
        throw 'npm.cmd was not found. Reinstall Node.js with npm.'
    }
    return $NpmCommand.Source
}

function Stop-RecordedProcess {
    param([Parameter(Mandatory = $true)][string]$PidFile)
    if (-not (Test-Path -LiteralPath $PidFile)) { return }

    $RawPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    $ProcessId = 0
    if ([int]::TryParse($RawPid, [ref]$ProcessId)) {
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $Process) {
            & taskkill.exe /PID $ProcessId /T /F | Out-Null
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Test-Url {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Wait-ForUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ErrorLog,
        [int]$TimeoutSeconds = 90
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        if (Test-Url -Url $Url) { return }
        $Process.Refresh()
        if ($Process.HasExited) {
            $Tail = ''
            if (Test-Path -LiteralPath $ErrorLog) {
                $Tail = (Get-Content -LiteralPath $ErrorLog -Tail 30 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            }
            throw "$Name exited before it became ready.`n$Tail"
        }
        Start-Sleep -Seconds 1
    }

    $Tail = ''
    if (Test-Path -LiteralPath $ErrorLog) {
        $Tail = (Get-Content -LiteralPath $ErrorLog -Tail 30 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds.`n$Tail"
}

function Initialize-Environment {
    Write-Host 'Initializing IoT IDS environment. This runs only when dependencies are missing or changed.'

    $Python = Find-PythonCommand
    $Npm = Get-NpmCommand
    Write-Host "Using Python $($Python.Version): $($Python.File)"

    if ($ForceInit -and (Test-Path -LiteralPath $VenvDir)) {
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    }

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host 'Creating Python virtual environment...'
        $VenvArgs = @($Python.Args) + @('-m', 'venv', $VenvDir)
        & $Python.File $VenvArgs
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
            throw 'Failed to create the Python virtual environment.'
        }
    }

    Write-Host 'Installing backend dependencies...'
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Failed to upgrade pip.' }
    & $VenvPython -m pip install -r (Join-Path $BackendDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install backend dependencies.' }

    if ($ForceInit -and (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules'))) {
        Remove-Item -LiteralPath (Join-Path $FrontendDir 'node_modules') -Recurse -Force
    }

    Write-Host 'Installing frontend dependencies...'
    Push-Location $FrontendDir
    try {
        if (Test-Path -LiteralPath (Join-Path $FrontendDir 'package-lock.json')) {
            & $Npm ci
        }
        else {
            & $Npm install
        }
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install frontend dependencies.' }
    }
    finally {
        Pop-Location
    }

    Save-DependencyState
    Write-Host 'Environment initialization completed.'
}

$StartedBackend = $false
$StartedFrontend = $false

try {
    $NeedsInit = $ForceInit -or
                 -not (Test-Path -LiteralPath $VenvPython) -or
                 -not (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules\.bin\vite.cmd')) -or
                 -not (Test-DependencyState)

    if ($NeedsInit) {
        Stop-RecordedProcess -PidFile $FrontendPidFile
        Stop-RecordedProcess -PidFile $BackendPidFile
        Initialize-Environment
    }
    else {
        Write-Host 'Existing environment is valid. Initialization skipped.'
    }

    $Npm = Get-NpmCommand

    $BackendReady = Test-Url -Url $BackendUrl
    $FrontendReady = Test-Url -Url $FrontendUrl
    if ($BackendReady -and $FrontendReady) {
        Write-Host 'IoT IDS is already running.'
        Start-Process $FrontendUrl
        exit 0
    }

    $BackendOut = Join-Path $RuntimeDir 'backend.log'
    $BackendErr = Join-Path $RuntimeDir 'backend-error.log'
    $FrontendOut = Join-Path $RuntimeDir 'frontend.log'
    $FrontendErr = Join-Path $RuntimeDir 'frontend-error.log'

    $env:PYTHONUTF8 = '1'
    $env:IOT_IDS_DEBUG = '0'

    if (-not $BackendReady) {
        Stop-RecordedProcess -PidFile $BackendPidFile
        Remove-Item -LiteralPath $BackendOut, $BackendErr -Force -ErrorAction SilentlyContinue
        Write-Host 'Starting backend...'
        $BackendProcess = Start-Process -FilePath $VenvPython -ArgumentList @('app.py') -WorkingDirectory $BackendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr
        Set-Content -LiteralPath $BackendPidFile -Value $BackendProcess.Id -Encoding ASCII
        $StartedBackend = $true
        Wait-ForUrl -Url $BackendUrl -Process $BackendProcess -Name 'Backend' -ErrorLog $BackendErr -TimeoutSeconds 90
    }
    else {
        Write-Host 'Backend is already running.'
    }

    if (-not $FrontendReady) {
        Stop-RecordedProcess -PidFile $FrontendPidFile
        Remove-Item -LiteralPath $FrontendOut, $FrontendErr -Force -ErrorAction SilentlyContinue
        Write-Host 'Starting frontend...'
        $FrontendProcess = Start-Process -FilePath $Npm -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1') -WorkingDirectory $FrontendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $FrontendOut -RedirectStandardError $FrontendErr
        Set-Content -LiteralPath $FrontendPidFile -Value $FrontendProcess.Id -Encoding ASCII
        $StartedFrontend = $true
        Wait-ForUrl -Url $FrontendUrl -Process $FrontendProcess -Name 'Frontend' -ErrorLog $FrontendErr -TimeoutSeconds 90
    }
    else {
        Write-Host 'Frontend is already running.'
    }

    Write-Host 'IoT IDS started successfully: http://127.0.0.1:3000/'
    Start-Process $FrontendUrl
    exit 0
}
catch {
    if ($StartedFrontend) { Stop-RecordedProcess -PidFile $FrontendPidFile }
    if ($StartedBackend) { Stop-RecordedProcess -PidFile $BackendPidFile }
    Write-Host ''
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('Runtime logs: ' + $RuntimeDir)
    exit 1
}

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackendDir = Join-Path $ProjectRoot 'backend'
$BackendApp = Join-Path $BackendDir 'app.py'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$ViteCli = Join-Path $FrontendDir 'node_modules\vite\bin\vite.js'
$BootstrapScript = Join-Path $ProjectRoot 'scripts\bootstrap-demo-database.py'
$LoginUrl = 'http://127.0.0.1:3000/login'
$ApiRoot = 'http://127.0.0.1:5000'
$KnownLocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($KnownLocalAppData)) { $KnownLocalAppData = $env:LOCALAPPDATA }
$DatabasePath = Join-Path $KnownLocalAppData 'IoT-IDS\demo.sqlite'
$DatabaseLockPath = "$DatabasePath.bootstrap.lock"
$BackupDir = Join-Path (Split-Path -Parent $DatabasePath) 'backups'
$RuntimeDir = Join-Path $KnownLocalAppData 'IoT-IDS\runtime'
$LogDir = Join-Path $KnownLocalAppData 'IoT-IDS\logs'
$BackendLog = Join-Path $LogDir 'backend.stdout.log'
$BackendErrorLog = Join-Path $LogDir 'backend.stderr.log'
$FrontendLog = Join-Path $LogDir 'frontend.stdout.log'
$FrontendErrorLog = Join-Path $LogDir 'frontend.stderr.log'
$StartedBackend = $null
$StartedFrontend = $null
$script:DemoDatabaseBootstrapped = $false

function Write-Status([string]$Message) {
    Write-Host "[IoT-IDS] $Message"
}

function Get-ListenPids([int]$Port) {
    $ids = @()
    try {
        $connections = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
        $ids += $connections | ForEach-Object { [int]$_.OwningProcess }
    } catch {
        $lines = netstat.exe -ano -p tcp 2>$null
        foreach ($line in $lines) {
            if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
                $ids += [int]$Matches[1]
            }
        }
    }
    return @($ids | Sort-Object -Unique)
}

function Get-ProcessRecord([int]$Id) {
    try {
        return Get-CimInstance Win32_Process -Filter "ProcessId=$Id" -ErrorAction Stop
    } catch {
        try { return Get-WmiObject Win32_Process -Filter "ProcessId=$Id" -ErrorAction Stop }
        catch { return $null }
    }
}

function Test-CommandContains([string]$CommandLine, [string]$Value) {
    if (-not $CommandLine) { return $false }
    return $CommandLine.IndexOf($Value, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-Secret([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $secret = (Get-Content -LiteralPath $Path -Raw).Trim()
        if ($secret.Length -lt 32) { throw "Secret file is invalid: $Path" }
        return $secret
    }
    $bytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    $secret = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    [IO.File]::WriteAllText($Path, $secret, [Text.Encoding]::ASCII)
    return $secret
}

function Test-BackendHealth {
    try {
        $health = Invoke-RestMethod -Uri "$ApiRoot/api/health" -TimeoutSec 3
        return ($health.status -eq 'ok' -and
            $health.database.available -eq $true -and
            $health.database.v3_schema_ready -eq $true)
    } catch {
        return $false
    }
}

function Test-ProjectBackendProcess($Record) {
    if (-not $Record) { return $false }
    $commandLine = [string]$Record.CommandLine
    foreach ($requiredArgument in @(
        $BackendApp,
        '-m flask',
        '--host 127.0.0.1',
        '--port 5000',
        '--no-reload'
    )) {
        if (-not (Test-CommandContains $commandLine $requiredArgument)) { return $false }
    }
    return $true
}

function Test-FrontendHealth {
    $request = $null
    $response = $null
    $reader = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($LoginUrl)
        $request.Timeout = 3000
        $response = $request.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        $content = $reader.ReadToEnd()
        return ($response.StatusCode -eq [System.Net.HttpStatusCode]::OK -and $content.Contains('IoT IDS'))
    } catch {
        return $false
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Wait-ForBackend([int]$ProcessId) {
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        if (Test-BackendHealth) { return $true }
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $process) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-ForFrontend([int]$ProcessId) {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-FrontendHealth) { return $true }
        $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $process) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $false
}
function ConvertTo-PlainTextFromSecureString([Security.SecureString]$Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Invoke-V3UpgradeCli([string[]]$Arguments) {
    $output = @()
    $exitCode = 1
    Push-Location $BackendDir
    try {
        $output = @(& $PythonPath -m v3_db_upgrade @Arguments)
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw "v3 database command failed with exit code $exitCode."
    }
    try {
        return (($output -join [Environment]::NewLine) | ConvertFrom-Json)
    } catch {
        throw "v3 database command did not return valid JSON: $($_.Exception.Message)"
    }
}

function Initialize-MissingDemoDatabase {
    if ([IO.File]::Exists($DatabasePath)) { return }

    $databaseDirectory = Split-Path -Parent $DatabasePath
    New-Item -ItemType Directory -Path $databaseDirectory -Force | Out-Null
    $sidecars = @("$DatabasePath-wal", "$DatabasePath-shm", "$DatabasePath-journal") |
        Where-Object { [IO.File]::Exists($_) }
    if ($sidecars.Count -gt 0) {
        throw "The target database is absent but sidecars exist: $($sidecars -join ', '). No initialization was attempted."
    }

    $lockStream = $null
    $lockOwned = $false
    try {
        try {
            $lockStream = [IO.File]::Open(
                $DatabaseLockPath,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            $lockOwned = $true
        } catch {
            throw "A database bootstrap lock already exists or cannot be created: $DatabaseLockPath. No database was touched."
        }

        if ([IO.File]::Exists($DatabasePath)) {
            throw "The target database appeared during bootstrap. It was left untouched; rerun after verifying it."
        }
        $sidecars = @("$DatabasePath-wal", "$DatabasePath-shm", "$DatabasePath-journal") |
            Where-Object { [IO.File]::Exists($_) }
        if ($sidecars.Count -gt 0) {
            throw "SQLite sidecar files appeared during bootstrap: $($sidecars -join ', '). No database was touched."
        }

        Write-Status "No demo database exists at the configured path. Creating a fresh local database."
        $firstSecure = Read-Host 'Set the initial Web administrator password' -AsSecureString
        $confirmSecure = Read-Host 'Confirm the Web administrator password' -AsSecureString
        $adminPassword = ConvertTo-PlainTextFromSecureString $firstSecure
        $confirmPassword = ConvertTo-PlainTextFromSecureString $confirmSecure
        $firstSecure.Dispose()
        $confirmSecure.Dispose()
        if (-not [String]::Equals($adminPassword, $confirmPassword, [StringComparison]::Ordinal)) {
            throw 'The administrator passwords did not match; no database was initialized.'
        }
        if ($adminPassword.Length -lt 6 -or $adminPassword.Length -gt 1024) {
            throw 'The administrator password must contain 6 to 1024 characters; no database was initialized.'
        }

        $bootstrapEnvironmentNames = @(
            'IOT_IDS_DATABASE_PATH',
            'IOT_IDS_BOOTSTRAP_ADMIN_USERNAME',
            'IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD'
        )
        $previousBootstrapEnvironment = @{}
        foreach ($name in $bootstrapEnvironmentNames) {
            $previousBootstrapEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        }
        $bootstrapExitCode = 1
        try {
            $env:IOT_IDS_DATABASE_PATH = $DatabasePath
            $env:IOT_IDS_BOOTSTRAP_ADMIN_USERNAME = 'admin'
            $env:IOT_IDS_BOOTSTRAP_ADMIN_PASSWORD = $adminPassword
            Push-Location $BackendDir
            try {
                & $PythonPath $BootstrapScript
                $bootstrapExitCode = $LASTEXITCODE
            } finally {
                Pop-Location
            }
        } finally {
            foreach ($name in $bootstrapEnvironmentNames) {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $previousBootstrapEnvironment[$name],
                    'Process'
                )
            }
            Remove-Variable adminPassword, confirmPassword -ErrorAction SilentlyContinue
        }
        if ($bootstrapExitCode -ne 0) {
            throw "The project database initializer failed with exit code $bootstrapExitCode. The file was not overwritten."
        }

        $prePlan = Invoke-V3UpgradeCli -Arguments @(
            'plan', '--database', $DatabasePath, '--json'
        )
        if ($prePlan.integrity_ok -ne $true -or $prePlan.current_schema_version -ne 0) {
            throw 'Read-only inspection did not identify a clean, unversioned fresh database; migration was refused.'
        }
        if (@($prePlan.pending_migrations).Count -ne 9) {
            throw 'Read-only inspection did not find the expected nine pending v3 migrations; migration was refused.'
        }
        if (@($prePlan.migration_ledger_issues).Count -gt 0 -or @($prePlan.schema_drift).Count -gt 0) {
            throw 'Read-only inspection found migration ledger issues or schema drift; migration was refused.'
        }
        if (@($prePlan.object_actions | Where-Object { $_.action -eq 'type_conflict' }).Count -gt 0) {
            throw 'Read-only inspection found a schema object type conflict; migration was refused.'
        }
        $requiredTables = @('users', 'alerts', 'traffic_logs', 'audit_logs', 'assets', 'policies', 'config', 'rules')
        foreach ($table in $requiredTables) {
            if (@($prePlan.old_tables) -notcontains $table) {
                throw "Read-only inspection found the new base schema incomplete (missing $table); migration was refused."
            }
        }

        New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
        $applyResult = Invoke-V3UpgradeCli -Arguments @(
            'apply', '--database', $DatabasePath,
            '--backup-directory', $BackupDir, '--json'
        )
        if ($applyResult.current_schema_version -ne 9 -or
            @($applyResult.post_integrity_check) -notcontains 'ok' -or
            @($applyResult.applied_versions).Count -ne 9) {
            throw 'v3 migration did not complete and verify all nine versions.'
        }

        $postPlan = Invoke-V3UpgradeCli -Arguments @(
            'plan', '--database', $DatabasePath, '--json'
        )
        if ($postPlan.integrity_ok -ne $true -or
            $postPlan.current_schema_version -ne 9 -or
            @($postPlan.pending_migrations).Count -ne 0 -or
            @($postPlan.migration_ledger_issues).Count -gt 0 -or
            @($postPlan.schema_drift).Count -gt 0) {
            throw 'Post-migration read-only inspection did not verify a complete v3 schema.'
        }

        $script:DemoDatabaseBootstrapped = $true
        Write-Status "Fresh database initialized at $DatabasePath."
        Write-Status "Read-only plan and integrity check passed; v3 schema version 9 is ready."
        Write-Status "Verified pre-migration backup: $($applyResult.backup.path)"
        Write-Status 'Web administrator username: admin. Use the password entered above.'
    } finally {
        if ($lockStream) { $lockStream.Dispose() }
        if ($lockOwned -and [IO.File]::Exists($DatabaseLockPath)) {
            Remove-Item -LiteralPath $DatabaseLockPath -Force -ErrorAction SilentlyContinue
        }
    }
}
try {
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null


    if (-not (Test-Path -LiteralPath $ViteCli -PathType Leaf)) {
        throw 'Frontend dependencies are missing. Restore frontend dependencies before launching.'
    }

    $pythonCandidates = @()
    $pythonCandidates += Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $pythonCandidates += Join-Path $BackendDir '.venv\Scripts\python.exe'
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $launchedPython = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $launchedPython) { $pythonCandidates += ([string]$launchedPython).Trim() }
    }
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($systemPython) { $pythonCandidates += $systemPython.Source }

    $PythonPath = $null
    foreach ($candidate in @($pythonCandidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        Push-Location $BackendDir
        try {
            & $candidate -c 'import flask, flask_cors, onnxruntime; from app import create_app' 2>$null
            if ($LASTEXITCODE -eq 0) { $PythonPath = (Resolve-Path -LiteralPath $candidate).Path; break }
        } catch {
        } finally {
            Pop-Location
        }
    }
    if (-not $PythonPath) { throw 'No installed Python with Flask, Flask-CORS, and ONNX Runtime was found.' }

    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) { throw 'node.exe was not found on PATH.' }

    Initialize-MissingDemoDatabase

    $backendOwners = @(Get-ListenPids 5000)
    $frontendOwners = @(Get-ListenPids 3000)
    $backendReady = $false
    $frontendReady = $false

    if ($backendOwners.Count -gt 0 -and $script:DemoDatabaseBootstrapped) {
        if ($backendOwners.Count -ne 1) {
            throw 'Multiple processes are listening on port 5000. No process was stopped.'
        }
        $confirmedBackendOwners = @()
        foreach ($ownerId in $backendOwners) {
            $record = Get-ProcessRecord ([int]$ownerId)
            $isProjectBackend = Test-ProjectBackendProcess $record
            if (-not $isProjectBackend) {
                throw 'Port 5000 has a listener that cannot be confirmed as this project. It was left running.'
            }
            $confirmedBackendOwners += [int]$ownerId
        }
        Write-Status 'Restarting the confirmed project backend so it uses the newly initialized user-local database.'
        foreach ($ownerId in $confirmedBackendOwners) {
            Stop-Process -Id $ownerId -Force -ErrorAction Stop
        }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (@(Get-ListenPids 5000).Count -eq 0) { break }
            Start-Sleep -Milliseconds 500
        }
        $backendOwners = @(Get-ListenPids 5000)
        if ($backendOwners.Count -gt 0) {
            throw 'The confirmed project backend did not release port 5000 after restart was requested.'
        }
    }

    if ($backendOwners.Count -gt 0) {
        if ($backendOwners.Count -ne 1) {
            throw 'Multiple processes are listening on port 5000. They were left running.'
        }
        $ownerId = [int]$backendOwners[0]
        $record = Get-ProcessRecord $ownerId
        if (-not (Test-ProjectBackendProcess $record) -or -not (Test-BackendHealth)) {
            throw 'Port 5000 is occupied by an unverified process. It was left running; inspect it before retrying.'
        }
        $backendReady = $true
        Write-Status 'Reusing the healthy project backend discovered on port 5000.'
    }
    if ($frontendOwners.Count -gt 0) {
        if ($frontendOwners.Count -ne 1) {
            throw 'Multiple processes are listening on port 3000. They were left running.'
        }
        $ownerId = [int]$frontendOwners[0]
        $record = Get-ProcessRecord $ownerId
        $validOwner = $record -and
            (Test-CommandContains ([string]$record.CommandLine) $ViteCli) -and
            (Test-CommandContains ([string]$record.CommandLine) '--port 3000') -and
            (Test-CommandContains ([string]$record.CommandLine) '--host 127.0.0.1')
        if (-not $validOwner -or -not (Test-FrontendHealth)) {
            throw 'Port 3000 is occupied by an unverified process. It was left running; inspect it before retrying.'
        }
        $frontendReady = $true
        Write-Status 'Reusing the verified project frontend discovered on port 3000.'
    }
    if (-not $backendReady) {
        $oldEnvironment = @{}
        $environmentNames = @(
            'IOT_IDS_DATABASE_PATH', 'IOT_IDS_SESSION_SECRET',
            'IOT_IDS_MOBILE_TOKEN_SECRET', 'IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP',
            'IOT_IDS_MQTT_ENABLED', 'IOT_IDS_ENV'
        )
        foreach ($name in $environmentNames) { $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
        try {
            $env:IOT_IDS_DATABASE_PATH = $DatabasePath
            $env:IOT_IDS_SESSION_SECRET = Get-Secret (Join-Path $RuntimeDir 'session-secret.txt')
            $env:IOT_IDS_MOBILE_TOKEN_SECRET = Get-Secret (Join-Path $RuntimeDir 'mobile-token-secret.txt')
            $env:IOT_IDS_MOBILE_ALLOW_INSECURE_HTTP = 'true'
            $env:IOT_IDS_MQTT_ENABLED = 'false'
            $env:IOT_IDS_ENV = 'development'
            $appSpec = '"' + $BackendApp + ':create_app"'
            $arguments = "-m flask --app $appSpec run --host 127.0.0.1 --port 5000 --no-reload"
            $StartedBackend = Start-Process -FilePath $PythonPath -ArgumentList $arguments -WorkingDirectory $BackendDir -WindowStyle Hidden -RedirectStandardOutput $BackendLog -RedirectStandardError $BackendErrorLog -PassThru
        } finally {
            foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], 'Process') }
        }
        Write-Status "Started backend (PID $($StartedBackend.Id)); waiting for database and v3 health."
        if (-not (Wait-ForBackend $StartedBackend.Id)) { throw "Backend did not become healthy. See $BackendErrorLog" }
    }

    if (-not $frontendReady) {
        $arguments = '"' + $ViteCli + '" --host 127.0.0.1 --port 3000 --strictPort'
        $StartedFrontend = Start-Process -FilePath $node.Source -ArgumentList $arguments -WorkingDirectory $FrontendDir -WindowStyle Hidden -RedirectStandardOutput $FrontendLog -RedirectStandardError $FrontendErrorLog -PassThru
        Write-Status "Started frontend (PID $($StartedFrontend.Id)); waiting for Vite."
        if (-not (Wait-ForFrontend $StartedFrontend.Id)) { throw "Frontend did not become ready. See $FrontendErrorLog" }
    }

    $health = Invoke-RestMethod -Uri "$ApiRoot/api/health" -TimeoutSec 3
    Write-Status "Frontend: $LoginUrl"
    Write-Status "Backend:  $ApiRoot (health $($health.status), schema $($health.database.schema_version), v3 ready)"
    Write-Status "Database: $DatabasePath"
    Write-Status 'MQTT remains disabled. Backend is bound to localhost only.'
    exit 0
} catch {
    if ($StartedFrontend) { Stop-Process -Id $StartedFrontend.Id -Force -ErrorAction SilentlyContinue }
    if ($StartedBackend) { Stop-Process -Id $StartedBackend.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "[IoT-IDS ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Backend log: $BackendErrorLog"
    Write-Host "Frontend log: $FrontendErrorLog"
    exit 1
}
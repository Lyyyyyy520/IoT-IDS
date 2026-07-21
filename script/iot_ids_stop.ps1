$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root '.runtime'

function Stop-ProcessTreeFromPidFile {
    param(
        [Parameter(Mandatory = $true)][string]$PidFile,
        [Parameter(Mandatory = $true)][string]$Name
    )

    if (-not (Test-Path -LiteralPath $PidFile)) {
        Write-Host "$Name is not recorded as running."
        return
    }

    $RawPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    $ProcessId = 0
    if (-not [int]::TryParse($RawPid, [ref]$ProcessId)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host "$Name PID file was invalid and has been removed."
        return
    }

    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $Process) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Host "$Name was already stopped."
        return
    }

    & taskkill.exe /PID $ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stop $Name (PID $ProcessId)."
    }

    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "$Name stopped."
}

if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    Write-Host 'IoT IDS is not recorded as running.'
    exit 0
}

Stop-ProcessTreeFromPidFile -PidFile (Join-Path $RuntimeDir 'frontend.pid') -Name 'Frontend'
Stop-ProcessTreeFromPidFile -PidFile (Join-Path $RuntimeDir 'backend.pid') -Name 'Backend'
Write-Host 'IoT IDS services have been stopped.'

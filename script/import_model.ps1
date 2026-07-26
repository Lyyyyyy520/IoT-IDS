param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$BundleDir = Join-Path $Root 'model_bundle'
$TargetDir = Join-Path $Root 'backend\data'
$ConfigPath = Join-Path $TargetDir 'model_config.json'

$Required = @(
    'best_model.ts',
    'best_model.pt',
    'feature_schema.json',
    'scaler.json',
    'scaler.pkl'
)

Write-Host 'Importing the trained CNN+LSTM model bundle...'
if (-not (Test-Path -LiteralPath $BundleDir)) {
    throw "Model bundle directory not found: $BundleDir"
}

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
foreach ($Name in $Required) {
    $Source = Join-Path $BundleDir $Name
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Required model file is missing: $Source"
    }
    Copy-Item -LiteralPath $Source -Destination (Join-Path $TargetDir $Name) -Force
}

# Absolute paths stored by an older project copy can point to the wrong folder.
Remove-Item -LiteralPath $ConfigPath -Force -ErrorAction SilentlyContinue

$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $VenvPython) {
    Write-Host 'Verifying model with the existing project environment...'
    & $VenvPython (Join-Path $Root 'training\verify_model.py')
    if ($LASTEXITCODE -ne 0) {
        throw 'Model files were copied, but verification failed. Run 重新初始化环境.bat and retry.'
    }
    Write-Host ''
    Write-Host 'Model import completed and verification passed.' -ForegroundColor Green
}
else {
    Write-Host ''
    Write-Host 'Model files were imported.' -ForegroundColor Green
    Write-Host 'The Python environment does not exist yet. Run 重新初始化环境.bat once, then run import_model.bat again for verification.'
}

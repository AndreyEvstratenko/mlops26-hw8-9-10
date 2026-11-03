param([switch]$WithModel)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Push-Location -LiteralPath $root
try {
    $env:UV_CACHE_DIR = Join-Path $root '.runs\uv-cache'
    $env:PYTHONUTF8 = '1'
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        & uv venv --python 3.12 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.12 environment.' }
    }
    & uv pip sync --python $python requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if ($WithModel) {
        & uv pip install --python $python -r requirements-model.txt
        if ($LASTEXITCODE -ne 0) { throw 'Model dependency installation failed.' }
    }
    Write-Host 'Setup completed. Run run_tests.cmd.' -ForegroundColor Green
} finally { Pop-Location }

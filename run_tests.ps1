param([switch]$NoPause)
$ErrorActionPreference = 'Stop'
$code = 1
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:PYTHONUTF8 = '1'
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.cmd first.' }
    & $python -u check.py
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Write-Host 'ALL FAST TESTS PASSED. Candidate quality rejection is expected.' -ForegroundColor Green
    } else { Write-Host 'TESTS FAILED. See evidence/checks.' -ForegroundColor Red }
} catch { Write-Host $_.Exception.Message -ForegroundColor Red }
finally {
    Pop-Location
    if (-not $NoPause) { Read-Host 'Press Enter to close' }
}
exit $code

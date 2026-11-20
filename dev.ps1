# OriginPulse dev launcher for Windows (no make/docker required).
#   .\dev.ps1           install deps if needed, then run API (:8000) + dashboard (:3000)
#   .\dev.ps1 -Setup    only install dependencies
#   .\dev.ps1 -Test     run the backend test suite
param([switch]$Setup, [switch]$Test)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating Python virtualenv..."
    py -3 -m venv .venv
}
& $py -m pip install -q -r backend\requirements.txt

if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    npm --prefix frontend install --no-fund --no-audit
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example"
}

if ($Setup) { & $py -m backend.app.cli init; exit 0 }
if ($Test) { & $py -m pytest -q; exit $LASTEXITCODE }

& $py -m backend.app.cli init
$api = Start-Process -FilePath $py -ArgumentList "-m","uvicorn","backend.app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $root -PassThru -NoNewWindow
try {
    Write-Host "API:       http://127.0.0.1:8000/docs"
    Write-Host "Dashboard: http://localhost:3000"
    npm --prefix frontend run dev
}
finally {
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}

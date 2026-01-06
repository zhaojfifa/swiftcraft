# run-dev.ps1
# One-command dev runner for SwiftCraft (Windows PowerShell)
# - Backend:  http://localhost:10000
# - Frontend: http://localhost:3000

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $repo "backend"
$frontendDir = Join-Path $repo "frontend"

$backendPort = 10000
$frontendPort = 3000
$apiBase = "http://localhost:$backendPort"

Write-Host "Repo: $repo"
Write-Host "API Base: $apiBase"
Write-Host "Backend: http://localhost:$backendPort/health"
Write-Host "Frontend: http://localhost:$frontendPort"

# --- Backend window ---
$backendCmd = @"
cd "$backendDir"
if (Test-Path ".\.venv\Scripts\Activate.ps1") { . .\.venv\Scripts\Activate.ps1 }
pip show python-multipart > $null 2>&1; if (`$LASTEXITCODE -ne 0) { pip install python-multipart }
`$env:USE_MOCK_AI="true"
`$env:AKOOL_DRY_RUN="true"
uvicorn app.main:app --reload --port $backendPort
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd | Out-Null
Start-Sleep -Seconds 1

# --- Frontend window ---
$frontendCmd = @"
cd "$frontendDir"
`$env:NEXT_PUBLIC_API_BASE="$apiBase"
npm run dev
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd | Out-Null

Write-Host ""
Write-Host "Started."
Write-Host "Open:"
Write-Host "  Frontend: http://localhost:$frontendPort"
Write-Host "  Backend health: http://localhost:$backendPort/health"
Write-Host "  Preset video: http://localhost:$backendPort/static/presets/swap/baseline_demo.mp4"

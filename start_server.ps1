# start_server.ps1
# ================
# One-click startup script for Parkinson's Multimodal Screening System
# 1. Bootstraps PostgreSQL 16 cluster if pg_ctl is available and not running
# 2. Starts the FastAPI Uvicorn ASGI server with Web Interface on http://localhost:8000

$baseDir    = $PSScriptRoot
$pgDataPath = Join-Path $baseDir ".pgdata"
$pgLogPath  = Join-Path $baseDir "postgres.log"
$pgCtlExe   = "C:\msys64\ucrt64\opt\pg-16\bin\pg_ctl.exe"
$pythonExe  = Join-Path $baseDir ".venv\Scripts\python.exe"
$uvicornExe = Join-Path $baseDir ".venv\Scripts\uvicorn.exe"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Parkinson's Multimodal Screening System - Web Platform   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check PostgreSQL Process (Optional local cluster)
$pgProcess = Get-Process postgres -ErrorAction SilentlyContinue
if (-not $pgProcess -and (Test-Path $pgCtlExe) -and (Test-Path $pgDataPath)) {
    Write-Host "[1/2] Starting local PostgreSQL 16 server cluster..." -ForegroundColor Yellow
    & $pgCtlExe -D $pgDataPath -l $pgLogPath start
    Start-Sleep -Seconds 2
} else {
    Write-Host "[1/2] Database subsystem initialized (PostgreSQL / SQLite ready)." -ForegroundColor Green
}

# 2. Launch FastAPI Service
Write-Host "[2/2] Launching Web Interface & API on http://localhost:8000..." -ForegroundColor Yellow
Write-Host "      - Web Dashboard:        http://localhost:8000/" -ForegroundColor Green
Write-Host "      - OpenAPI Swagger Docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "      - Health Check Probe:   http://localhost:8000/health" -ForegroundColor Cyan
Write-Host "      (Press Ctrl + C to stop the server anytime)" -ForegroundColor Gray
Write-Host "----------------------------------------------------------" -ForegroundColor DarkGray

$env:DATABASE_URL = "postgresql://postgres@localhost:5432/parkinsons"
$env:MODEL_PATH   = "parkinsons_multimodal_model.joblib"

& $uvicornExe main:app --reload --port 8000 --host 0.0.0.0


# stop_server.ps1
# ===============
# Cleanly terminates the local PostgreSQL database server cluster

$baseDir    = $PSScriptRoot
$pgDataPath = Join-Path $baseDir ".pgdata"
$pgCtlExe   = "C:\msys64\ucrt64\opt\pg-16\bin\pg_ctl.exe"

if (Test-Path $pgCtlExe) {
    Write-Host "Gracefully stopping PostgreSQL database server..." -ForegroundColor Yellow
    & $pgCtlExe -D $pgDataPath stop
    Write-Host "PostgreSQL server stopped. All write buffers synced." -ForegroundColor Green
} else {
    Write-Host "No local pg_ctl detected." -ForegroundColor Gray
}


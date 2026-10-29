@echo off
echo ==========================================================
echo  Parkinson's Multimodal Screening System - Web Platform
echo ==========================================================

set SCRIPT_DIR=%~dp0
set PGDATA=%SCRIPT_DIR%.pgdata
set PGLOG=%SCRIPT_DIR%postgres.log
set PGCTL=C:\msys64\ucrt64\opt\pg-16\bin\pg_ctl.exe
set UVICORN=%SCRIPT_DIR%.venv\Scripts\uvicorn.exe

tasklist /FI "IMAGENAME eq postgres.exe" 2>NUL | find /I /N "postgres.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [1/2] PostgreSQL is already active on port 5432.
) else (
    if exist "%PGCTL%" (
        echo [1/2] Starting local PostgreSQL 16 server cluster...
        "%PGCTL%" -D "%PGDATA%" -l "%PGLOG%" start
        timeout /t 2 /nobreak >nul
    ) else (
        echo [1/2] PostgreSQL daemon not found, SQLite/Direct SQL mode ready.
    )
)

echo [2/2] Launching Web Interface on http://localhost:8000...
echo       - Web Dashboard:        http://localhost:8000/
echo       - OpenAPI Swagger Docs: http://localhost:8000/docs
echo       - Health Check Probe:   http://localhost:8000/health
echo       (Press Ctrl + C to stop the server anytime)
echo ----------------------------------------------------------

set DATABASE_URL=postgresql://postgres@localhost:5432/parkinsons
set MODEL_PATH=parkinsons_multimodal_model.joblib

"%UVICORN%" main:app --reload --port 8000 --host 0.0.0.0


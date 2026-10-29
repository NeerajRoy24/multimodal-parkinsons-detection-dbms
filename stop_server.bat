@echo off
echo Gracefully stopping PostgreSQL database server...
set SCRIPT_DIR=%~dp0
set PGDATA=%SCRIPT_DIR%.pgdata
set PGCTL=C:\msys64\ucrt64\opt\pg-16\bin\pg_ctl.exe

if exist "%PGCTL%" (
    "%PGCTL%" -D "%PGDATA%" stop
    echo PostgreSQL server stopped.
) else (
    echo Local PostgreSQL executable not found.
)


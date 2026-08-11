@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo Starting Nexus Controller...
"%PY%" server\run_server.py %*
if errorlevel 1 pause

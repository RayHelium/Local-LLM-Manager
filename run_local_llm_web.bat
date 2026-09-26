@echo off
rem One-click start for Local LLM Manager (Web UI):
rem - runs in background via pythonw (no console window), this window exits
rem - if the service is already running, just opens the browser
cd /d %~dp0
set PORT=8090
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul
if %errorlevel%==0 (
    start http://127.0.0.1:%PORT%
) else (
    start "" C:\Python314\pythonw.exe local_llm_web.py --port %PORT% --no-browser
    timeout /t 2 >nul
    start http://127.0.0.1:%PORT%
)

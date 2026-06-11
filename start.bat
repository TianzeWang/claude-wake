@echo off
title Claude Wake
cd /d "%~dp0"
echo ============================================================
echo   Claude Wake launcher
echo ============================================================
echo.
echo [1/3] starting backend (web console)...
start "Claude Wake Backend (close = stop)" wsl.exe -e bash -lc "python3 app.py || { echo; echo [backend exited - see error above]; exec bash; }"
echo [2/3] opening the dashboard...
timeout /t 3 >nul
start msedge --app=http://localhost:8770
if errorlevel 1 start "" http://localhost:8770
echo [3/3] opening a terminal running Claude...
start "Claude Work" wsl.exe -e bash -lc "bash start_claude.sh; echo; echo [start_claude exited]; exec bash"
echo.
echo Done. The dashboard is in your browser. To STOP: close the Backend window.
pause

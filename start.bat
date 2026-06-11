@echo off
title Claude Wake
cd /d "%~dp0"
echo Starting Claude Wake...
start "Claude Wake Backend (close = stop)" /min wsl.exe -e bash -lc "python3 app.py || { echo; echo [backend exited - see error above]; exec bash; }"
timeout /t 3 >nul
start msedge --app=http://localhost:8770
if errorlevel 1 start "" http://localhost:8770
start "Claude Work" wsl.exe -e bash -lc "bash start_claude.sh; echo; echo [start_claude exited]; exec bash"
timeout /t 2 >nul
exit

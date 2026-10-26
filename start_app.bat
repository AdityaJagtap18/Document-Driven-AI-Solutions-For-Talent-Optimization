@echo off
REM Starts the CDPI frontend static server (port 8732) and the Azure
REM Functions backend host (port 7071) in two separate windows, same
REM commands .claude/launch.json uses for Claude Code's own preview.
REM Close either window (or Ctrl+C in it) to stop that server.

cd /d "%~dp0"

echo Starting frontend (http://127.0.0.1:8732) ...
start "CDPI Frontend (8732)" cmd /k "python -m http.server 8732 --directory frontend"

echo Starting backend / Azure Functions host (http://127.0.0.1:7071) ...
start "CDPI Backend (7071)" cmd /k "cd /d azure-functions && func start --port 7071"

echo.
echo Both servers are starting in their own windows.
echo Frontend: http://127.0.0.1:8732
echo Backend:  http://127.0.0.1:7071
pause

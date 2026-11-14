@echo off
rem Sound System launcher: setup if needed -> start server -> open browser.
setlocal
cd /d "%~dp0"

rem Port: SOUND_SYSTEM_PORT env override, default 8790
rem (8765 is permanently used by another app on this machine).
if not defined SOUND_SYSTEM_PORT set "SOUND_SYSTEM_PORT=8790"
set "PORT=%SOUND_SYSTEM_PORT%"

set "NEED_SETUP="
if not exist ".venv\Scripts\python.exe" set "NEED_SETUP=1"
if not exist "tools\ffmpeg\bin\ffmpeg.exe" set "NEED_SETUP=1"
if not exist "frontend\dist\index.html" set "NEED_SETUP=1"

if defined NEED_SETUP (
    echo == First-time setup ==
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
    if errorlevel 1 (
        echo.
        echo Setup failed. See the messages above.
        pause
        exit /b 1
    )
)

rem Check the port is free before starting (uvicorn would only show a bind traceback).
powershell -NoProfile -Command "try { $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.1'), %PORT%); $l.Start(); $l.Stop(); exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Port %PORT% is in use by another program. Set SOUND_SYSTEM_PORT to another port and run again.
    pause
    exit /b 1
)

rem Open the browser as soon as /api/health answers (background poller).
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\open-when-ready.ps1" -Port %PORT%

uv run uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
endlocal

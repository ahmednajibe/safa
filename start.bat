@echo off
rem Sound System launcher (M10): all logic lives in scripts\start.ps1 -
rem this shim only guarantees the right console encoding and bypasses
rem the default execution policy for this one script (no admin needed).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    pause
)
endlocal & exit /b %RC%

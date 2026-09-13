@echo off
cd /d "%~dp0"
where pwsh.exe >nul 2>nul
if errorlevel 1 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" -Interactive
) else (
    pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" -Interactive
)
set "BUILD_EXIT=%ERRORLEVEL%"
pause
exit /b %BUILD_EXIT%

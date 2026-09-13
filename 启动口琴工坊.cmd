@echo off
setlocal
if exist "%~dp0app\HarmonicaStudio\HarmonicaStudio.exe" (
  start "" "%~dp0app\HarmonicaStudio\HarmonicaStudio.exe"
) else (
  python "%~dp0launch.py" gui
  if errorlevel 1 pause
)

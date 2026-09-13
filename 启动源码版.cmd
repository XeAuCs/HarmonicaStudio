@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到项目的 Python 环境，请按 README 的开发说明配置 .venv。
  pause
  popd
  exit /b 1
)
".venv\Scripts\python.exe" "%~dp0launch.py" gui %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not "%LAUNCH_EXIT%"=="0" (
  echo 启动失败，请查看上方错误信息。
  pause
)
popd
exit /b %LAUNCH_EXIT%

@echo off
cd /d "%~dp0"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%CODEX_PY%" (
  echo Codex bundled Python was not found at:
  echo   %CODEX_PY%
  exit /b 1
)
"%CODEX_PY%" -m home_remote_desktop.client %*


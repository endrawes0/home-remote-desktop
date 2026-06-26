@echo off
cd /d "%~dp0"
setlocal

if /i "%~1"=="--help" goto help
if /i "%~1"=="/?" goto help
if /i "%~1"=="--performance" goto performance

call "%~dp0hrd-python.cmd" -m pip install -r requirements.txt
exit /b %errorlevel%

:help
echo Home Remote Desktop installer
echo.
echo Usage:
echo   install.bat
echo   install.bat --performance
echo.
echo --performance installs Python performance packages and attempts to install
echo libjpeg-turbo.libjpeg-turbo.VC using winget.
exit /b 0

:performance
call "%~dp0hrd-python.cmd" -m pip install -r requirements-performance.txt
if errorlevel 1 exit /b %errorlevel%

set "PY_ARCH=x64"
for /f "usebackq delims=" %%A in (`call "%~dp0hrd-python.cmd" -m home_remote_desktop.install_helpers python-arch`) do set "PY_ARCH=%%A"

where winget >nul 2>nul
if errorlevel 1 (
  echo.
  echo winget was not found. Python performance packages were installed, but libjpeg-turbo was not.
  echo Install libjpeg-turbo manually from https://libjpeg-turbo.org/ or install winget.
  exit /b 0
)

echo.
echo Installing libjpeg-turbo native DLL for Python architecture %PY_ARCH% with winget...
winget install --id libjpeg-turbo.libjpeg-turbo.VC -e --architecture %PY_ARCH% --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
  echo.
  echo libjpeg-turbo installation did not complete. The app will still run with Pillow JPEG.
  exit /b 0
)

echo.
echo Performance install complete.
echo If TurboJPEG is not detected automatically, pass:
echo   --turbojpeg-lib-path "C:\Program Files\libjpeg-turbo64\bin\turbojpeg.dll"

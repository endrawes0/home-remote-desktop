@echo off
where py >nul 2>nul
if %errorlevel%==0 (
  py --version >nul 2>nul
  if %errorlevel%==0 (
    py %*
    exit /b %errorlevel%
  )
)

where python >nul 2>nul
if %errorlevel%==0 (
  python --version >nul 2>nul
  if %errorlevel%==0 (
    python %*
    exit /b %errorlevel%
  )
)

echo Python was not found.
echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
echo During install, enable "Add python.exe to PATH".
exit /b 1

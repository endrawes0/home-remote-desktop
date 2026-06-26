@echo off
setlocal

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 --version >nul 2>nul
  if %errorlevel%==0 (
    py -3 %*
    exit /b %errorlevel%
  )
)

for %%P in (python.exe python3.exe) do (
  for /f "usebackq delims=" %%F in (`where %%P 2^>nul`) do (
    echo %%F | findstr /i "\\WindowsApps\\python" >nul
    if errorlevel 1 (
      "%%F" --version >nul 2>nul
      if not errorlevel 1 (
        "%%F" %*
        exit /b %errorlevel%
      )
    )
  )
)

echo.
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo During install, enable "Add python.exe to PATH".
echo.
echo Or install with:
echo   winget install Python.Python.3.12
echo.
exit /b 1


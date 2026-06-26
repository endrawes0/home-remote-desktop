@echo off
setlocal
set VERSION=8.10.2
set DIST=%USERPROFILE%\.gradle\wrapper\dists\gradle-%VERSION%-bin\hrd\gradle-%VERSION%
set ZIP=%USERPROFILE%\.gradle\wrapper\dists\gradle-%VERSION%-bin\hrd\gradle-%VERSION%-bin.zip
if exist "%DIST%\bin\gradle.bat" goto run
powershell -ExecutionPolicy Bypass -NoProfile -Command "$ErrorActionPreference='Stop'; $zip='%ZIP%'; $dist='%DIST%'; $parent=Split-Path $dist; New-Item -ItemType Directory -Force -Path (Split-Path $zip) | Out-Null; if (!(Test-Path $zip)) { Invoke-WebRequest -Uri 'https://services.gradle.org/distributions/gradle-%VERSION%-bin.zip' -OutFile $zip }; if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }; Expand-Archive -Path $zip -DestinationPath $parent"
:run
call "%DIST%\bin\gradle.bat" %*

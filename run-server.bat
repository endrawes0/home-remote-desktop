@echo off
cd /d "%~dp0"
call "%~dp0hrd-python.cmd" -m home_remote_desktop.server %*

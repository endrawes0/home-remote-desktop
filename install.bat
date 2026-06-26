@echo off
cd /d "%~dp0"
call "%~dp0python.cmd" -m pip install -r requirements.txt

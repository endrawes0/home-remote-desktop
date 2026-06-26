@echo off
cd /d "%~dp0"
call "%~dp0hrd-python.cmd" -m pip install -r requirements.txt

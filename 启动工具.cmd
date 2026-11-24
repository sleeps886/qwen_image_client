@echo off
setlocal
cd /d "%~dp0"
python -u -m backend.app.main
if errorlevel 1 pause

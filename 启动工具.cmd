@echo off
cd /d "%~dp0"
python -m backend.app.main
if errorlevel 1 pause

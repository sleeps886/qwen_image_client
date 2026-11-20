@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend\scripts\enable_lan_firewall.ps1"
if errorlevel 1 echo FAILED: Right-click this file and choose Run as administrator.
pause

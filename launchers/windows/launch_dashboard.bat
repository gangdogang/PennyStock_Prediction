@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_dashboard.ps1" %*
exit /b %errorlevel%

@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0live_api_setup.ps1" %*
exit /b %errorlevel%

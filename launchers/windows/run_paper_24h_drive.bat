@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_paper_24h_drive.ps1" %*
exit /b %errorlevel%

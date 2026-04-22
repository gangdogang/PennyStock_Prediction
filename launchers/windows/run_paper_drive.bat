@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_paper_drive.ps1" %*
exit /b %errorlevel%

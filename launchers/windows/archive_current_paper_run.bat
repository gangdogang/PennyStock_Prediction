@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0archive_current_paper_run.ps1" %*
exit /b %errorlevel%

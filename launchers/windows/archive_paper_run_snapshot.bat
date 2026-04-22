@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0archive_paper_run_snapshot.ps1" %*
exit /b %errorlevel%

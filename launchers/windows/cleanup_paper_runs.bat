@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup_paper_runs.ps1" %*
exit /b %errorlevel%

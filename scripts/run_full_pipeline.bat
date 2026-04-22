@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_full_pipeline.ps1" %*
exit /b %errorlevel%

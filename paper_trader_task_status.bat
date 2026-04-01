@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0paper_trader_task_status.ps1"
exit /b %errorlevel%

@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0remove_paper_trader_task.ps1"
exit /b %errorlevel%

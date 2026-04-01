@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_paper_trader_task.ps1"
exit /b %errorlevel%

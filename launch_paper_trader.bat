@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_paper_trader.ps1"
exit /b %errorlevel%

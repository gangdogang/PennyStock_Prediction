@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo ========================================
echo  Penny Stock Radar One-Click Launcher
echo ========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set "BASE_PY=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3 is not installed.
        pause
        exit /b 1
    )
    set "BASE_PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/6] Creating virtual environment ^(.venv^)...
    %BASE_PY% -m venv .venv
)

set "VENV_PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"
set "VENV_PIP=%ROOT_DIR%.venv\Scripts\pip.exe"
set "DB_PATH=%ROOT_DIR%data\penny_stock_radar.sqlite3"

echo [2/6] Checking dependencies...
"%VENV_PYTHON%" -c "import importlib.util, sys; req=['streamlit','typer','pandas','pydantic','httpx','yfinance']; sys.exit(0 if all(importlib.util.find_spec(m) for m in req) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages. This can take 1-3 minutes on the first run.
    "%VENV_PIP%" install -U pip
    "%VENV_PIP%" install -e ".[dev,ui]"
) else (
    echo Dependencies are already installed. Skipping package install.
)

if not exist ".env" if exist ".env.example" (
    echo [3/6] Creating .env from .env.example...
    copy /Y ".env.example" ".env" >nul
)

echo [4/6] Checking whether a full refresh is needed...
if exist "%DB_PATH%" (
    "%VENV_PYTHON%" -c "from datetime import datetime, timezone; import sqlite3, sys; conn=sqlite3.connect(sys.argv[1]); row=conn.execute('SELECT created_at FROM scan_runs ORDER BY created_at DESC LIMIT 1').fetchone(); conn.close(); import sys as _s; _s.exit(1 if (row is None or not row[0]) else (0 if (datetime.now(timezone.utc)-datetime.fromisoformat(str(row[0]).replace('Z','+00:00'))).total_seconds()/60 <= 15 else 1))" "%DB_PATH%" >nul 2>nul
    if errorlevel 1 (
        echo Running the full pipeline. This can take a little while.
        call "%ROOT_DIR%scripts\run_full_pipeline.bat"
        if errorlevel 1 exit /b 1
    ) else (
        echo Recent data found in the last 15 minutes. Skipping startup refresh.
        echo Use the sidebar button '전체 최신화 실행' when you want a full recalculation.
    )
) else (
    echo Running the full pipeline. This can take a little while.
    call "%ROOT_DIR%scripts\run_full_pipeline.bat"
    if errorlevel 1 exit /b 1
)

echo [5/6] Launching dashboard...
echo.
echo If the browser does not open automatically, visit:
echo http://localhost:8501
echo.
echo Press Ctrl+C in this terminal window to stop the server.
echo.

echo [6/6] Starting Streamlit
start "" http://localhost:8501
set "PYTHONPATH=%ROOT_DIR%src;%PYTHONPATH%"
"%VENV_PYTHON%" -m streamlit run "%ROOT_DIR%src\penny_stock_radar\ui\app.py" --server.address localhost --server.port 8501
endlocal

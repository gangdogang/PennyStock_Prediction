@echo off
setlocal

set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

if exist ".venv\Scripts\python.exe" (
    set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "VENV_PYTHON=py -3"
    ) else (
        set "VENV_PYTHON=python"
    )
)

set "PYTHONPATH=%ROOT_DIR%\src;%PYTHONPATH%"
set "DB_PATH=%ROOT_DIR%\data\penny_stock_radar.sqlite3"

if /I "%PENNY_STOCK_RESET_DB%"=="1" (
    if exist "%DB_PATH%" (
        echo [0/5] Resetting database because PENNY_STOCK_RESET_DB=1
        del /f /q "%DB_PATH%"
    )
)

echo [1/5] Initializing database
%VENV_PYTHON% -m penny_stock_radar init-db
if errorlevel 1 exit /b 1
echo [2/5] Building universe
%VENV_PYTHON% -m penny_stock_radar build-universe --max-symbols 40 --export-json "%ROOT_DIR%\sample_outputs\universe_candidates.sample.json"
if errorlevel 1 exit /b 1
echo [3/5] Building watchlist
%VENV_PYTHON% -m penny_stock_radar build-watchlist --limit 10 --lookback-hours 48
if errorlevel 1 exit /b 1
echo [4/5] Running replay pipeline
%VENV_PYTHON% -m penny_stock_radar run-replay-pipeline --output-csv "%ROOT_DIR%\sample_outputs\mock_replay.sample.csv" --export-json "%ROOT_DIR%\sample_outputs\replay_report.sample.json"
if errorlevel 1 exit /b 1
echo Skipping sample social analysis. Run .\scripts\psradar analyze-social --mentions-csv ^<real_social_mentions.csv^> when you have real mention data.
echo [5/5] Exporting summary
%VENV_PYTHON% -m penny_stock_radar export-summary --json-output "%ROOT_DIR%\sample_outputs\radar_summary.json" --markdown-output "%ROOT_DIR%\sample_outputs\radar_summary.md"
if errorlevel 1 exit /b 1
echo Pipeline completed successfully.

endlocal

@echo off
setlocal

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

if not exist ".env" if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
)

if not exist ".env" (
    echo .env.example was not found, so .env could not be created.
    pause
    exit /b 1
)

echo Opening .env in Notepad...
echo.
echo Fill in one of these:
echo.
echo PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca
echo PENNY_STOCK_ALPACA_API_KEY=
echo PENNY_STOCK_ALPACA_SECRET_KEY=
echo PENNY_STOCK_ALPACA_MARKET_DATA_FEED=iex
echo.
echo or
echo.
echo PENNY_STOCK_LIVE_MARKET_PROVIDER=polygon
echo PENNY_STOCK_POLYGON_API_KEY=
echo.
echo Optional Gemini review:
echo PENNY_STOCK_GEMINI_API_KEY=
echo PENNY_STOCK_GEMINI_MODEL=gemini-3-flash-preview
echo.

start "" notepad "%ROOT_DIR%.env"
endlocal

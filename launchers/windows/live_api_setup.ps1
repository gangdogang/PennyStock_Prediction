$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $RootDir

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
}

if (-not (Test-Path ".env")) {
    throw ".env.example was not found, so .env could not be created."
}

Write-Host "Opening .env in Notepad..."
Write-Host ""
Write-Host "Fill in one of these:"
Write-Host ""
Write-Host "PENNY_STOCK_LIVE_MARKET_PROVIDER=kis"
Write-Host "PENNY_STOCK_KIS_APP_KEY="
Write-Host "PENNY_STOCK_KIS_APP_SECRET="
Write-Host "PENNY_STOCK_KIS_NASDAQ_MASTER_PATH=./data/kis_master/NASMST.COD"
Write-Host "PENNY_STOCK_KIS_NYSE_MASTER_PATH=./data/kis_master/NYSMST.COD"
Write-Host "PENNY_STOCK_KIS_AMEX_MASTER_PATH=./data/kis_master/AMSMST.COD"
Write-Host ""
Write-Host "or"
Write-Host ""
Write-Host "PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca"
Write-Host "PENNY_STOCK_ALPACA_API_KEY="
Write-Host "PENNY_STOCK_ALPACA_SECRET_KEY="
Write-Host "PENNY_STOCK_ALPACA_MARKET_DATA_FEED=iex"
Write-Host ""
Write-Host "Optional Gemini review:"
Write-Host "PENNY_STOCK_GEMINI_API_KEY="
Write-Host "PENNY_STOCK_GEMINI_MODEL=gemini-3-flash-preview"
Write-Host ""

Start-Process notepad (Join-Path $RootDir ".env")

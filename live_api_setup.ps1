$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
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
Write-Host "PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca"
Write-Host "PENNY_STOCK_ALPACA_API_KEY="
Write-Host "PENNY_STOCK_ALPACA_SECRET_KEY="
Write-Host "PENNY_STOCK_ALPACA_MARKET_DATA_FEED=iex"
Write-Host ""
Write-Host "or"
Write-Host ""
Write-Host "PENNY_STOCK_LIVE_MARKET_PROVIDER=polygon"
Write-Host "PENNY_STOCK_POLYGON_API_KEY="
Write-Host ""

Start-Process notepad (Join-Path $RootDir ".env")

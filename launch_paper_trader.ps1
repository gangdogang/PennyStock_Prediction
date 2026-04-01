$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir
. (Join-Path $RootDir "scripts\windows_common.ps1")

Write-Host "========================================"
Write-Host " Penny Stock Radar Paper Trader"
Write-Host "========================================"
Write-Host ""

$runtime = Ensure-PennyStockRuntime -RootDir $RootDir
Initialize-PythonPathEnv -RootDir $RootDir

Write-Host "Starting automated paper trader..."
Write-Host ""
Write-Host "Outputs:"
Write-Host "  csv logs -> $(Join-Path $RootDir 'sample_outputs\paper_trading')"
Write-Host ""
Write-Host "Default loop: 60-second polling"
Write-Host "Press Ctrl+C in this terminal window to stop the paper trader."
Write-Host ""

& $runtime.VenvPython -m penny_stock_radar paper-trader --check-interval-seconds 60
Assert-LastExitCode "Starting paper trader"

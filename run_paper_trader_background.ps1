$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir
. (Join-Path $RootDir "scripts\windows_common.ps1")

$runtime = Ensure-PennyStockRuntime -RootDir $RootDir
$logs = Get-PaperTraderLogPaths -RootDir $RootDir
Initialize-PythonPathEnv -RootDir $RootDir

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logs.Stdout -Value "=== Paper trader background start $timestamp ==="
Add-Content -Path $logs.Stderr -Value "=== Paper trader background start $timestamp ==="

& $runtime.VenvPython -m penny_stock_radar paper-trader --check-interval-seconds 60 1>> $logs.Stdout 2>> $logs.Stderr
exit $LASTEXITCODE

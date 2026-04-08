$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $RootDir
. (Join-Path $RootDir "scripts\windows_common.ps1")

$port = 8501

Write-Host "========================================"
Write-Host " Penny Stock Radar LAN Dashboard"
Write-Host "========================================"
Write-Host ""

$runtime = Ensure-PennyStockRuntime -RootDir $RootDir -IncludeUi
Invoke-DashboardRefreshIfNeeded -RootDir $RootDir -VenvPython $runtime.VenvPython
Initialize-PythonPathEnv -RootDir $RootDir

Write-Host "[5/5] Starting Streamlit for LAN access"
Write-Host ""
Write-Host "Open from this desktop:"
Write-Host "  http://localhost:$port"
Write-Host ""

$lanUrls = Get-LanIPv4Addresses
if ($lanUrls.Count -gt 0) {
    Write-Host "Open from your Mac on the same network:"
    foreach ($ipAddress in $lanUrls) {
        Write-Host "  http://$($ipAddress):$port"
    }
}
else {
    Write-Host "A LAN IPv4 address was not detected automatically."
    Write-Host "Use this PC's IPv4 address with port $port."
}

Write-Host ""
Write-Host "Press Ctrl+C in this terminal window to stop the server."
Write-Host ""

& $runtime.VenvPython -m penny_stock_radar dashboard --host 0.0.0.0 --port $port --headless
Assert-LastExitCode "Starting LAN dashboard"

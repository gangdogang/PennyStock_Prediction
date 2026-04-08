$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $RootDir
. (Join-Path $RootDir "scripts\windows_common.ps1")

$runtime = Ensure-PennyStockRuntime -RootDir $RootDir
$logs = Get-AiSupervisorLogPaths -RootDir $RootDir
Initialize-PythonPathEnv -RootDir $RootDir

Rotate-LogFile -Path $logs.Stdout
Rotate-LogFile -Path $logs.Stderr

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logs.Stdout -Value "=== AI supervisor background start $timestamp ==="
Add-Content -Path $logs.Stderr -Value "=== AI supervisor background start $timestamp ==="

& $runtime.VenvPython -m penny_stock_radar ai-supervisor --run-once --refresh-if-older-than-minutes 15 1>> $logs.Stdout 2>> $logs.Stderr
exit $LASTEXITCODE

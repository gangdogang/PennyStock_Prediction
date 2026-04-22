$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = Join-Path $ScriptDir "run_paper_drive.ps1"

Write-Host "run_paper_24h_drive.ps1 is a compatibility alias."
Write-Host "Use run_paper_drive.ps1 for new runs."
Write-Host ""

& $Target @args
exit $LASTEXITCODE

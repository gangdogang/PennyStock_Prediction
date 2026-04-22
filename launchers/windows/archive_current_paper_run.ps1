$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = Join-Path $ScriptDir "archive_paper_run_snapshot.ps1"

Write-Host "archive_current_paper_run.ps1 is a compatibility alias."
Write-Host "Use archive_paper_run_snapshot.ps1 for new snapshots."
Write-Host ""

& $Target @args
exit $LASTEXITCODE

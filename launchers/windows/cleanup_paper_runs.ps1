param(
    [string]$DriveRoot = "",
    [int]$KeepLatestRuns = 20,
    [int]$DeleteOlderThanDays = 14,
    [switch]$DeleteFailedEmptyRuns,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $RootDir

function Resolve-DriveRoot {
    param([string]$Value)

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return $Value
    }

    $envRoot = [Environment]::GetEnvironmentVariable("PENNY_STOCK_PAPER_DRIVE_ROOT")
    if ([string]::IsNullOrWhiteSpace($envRoot)) {
        $envRoot = [Environment]::GetEnvironmentVariable("PENNY_STOCK_PAPER_24H_DRIVE_ROOT")
    }
    if (-not [string]::IsNullOrWhiteSpace($envRoot)) {
        return $envRoot
    }

    if ($env:OneDrive) {
        return (Join-Path $env:OneDrive "Penny_Stock_Runs")
    }
    if ($env:USERPROFILE) {
        return (Join-Path $env:USERPROFILE "Penny_Stock_Runs")
    }
    return (Join-Path $RootDir "sample_outputs\paper_drive")
}

function Read-ManifestStatus {
    param([string]$RunRoot)

    $manifestPath = Join-Path $RunRoot "launcher_manifest.json"
    if (-not (Test-Path $manifestPath)) {
        return ""
    }
    try {
        $manifest = Get-Content -Path $manifestPath -Raw | ConvertFrom-Json
        return [string]$manifest.status
    }
    catch {
        return ""
    }
}

function Has-TradeLog {
    param([string]$RunRoot)

    $tradeLog = Join-Path (Join-Path $RunRoot "paper_trading") "paper_trade_log.csv"
    return ((Test-Path $tradeLog) -and ((Get-Item $tradeLog).Length -gt 0))
}

function Remove-OrReport {
    param(
        [string]$Path,
        [string]$Reason
    )

    if ($Apply) {
        Remove-Item -Path $Path -Recurse -Force
        Write-Host "deleted: $Path"
    }
    else {
        Write-Host "would delete: $Path"
    }
    Write-Host "  reason: $Reason"
}

$DriveRoot = Resolve-DriveRoot -Value $DriveRoot
$runRoots = @(
    @(
        (Join-Path $DriveRoot "paper_runs"),
        (Join-Path $DriveRoot "paper_24h_runs")
    ) | Where-Object { Test-Path $_ }
)

Write-Host "Paper run cleanup"
Write-Host "Drive root: $DriveRoot"
Write-Host "Mode: $(if ($Apply) { 'apply' } else { 'dry-run' })"
Write-Host ""

if ($runRoots.Count -eq 0) {
    Write-Host "No paper run roots found."
    exit 0
}

$allRuns = @($runRoots | ForEach-Object { Get-ChildItem -Path $_ -Directory }) |
    Sort-Object LastWriteTime -Descending
$protected = @($allRuns | Select-Object -First $KeepLatestRuns).FullName
$cutoff = (Get-Date).AddDays(-1 * $DeleteOlderThanDays)
$deletedCount = 0

foreach ($runsRoot in $runRoots) {
    Get-ChildItem -Path $runsRoot -Directory -Recurse -Filter ".snapshot_staging_*" |
        ForEach-Object {
            Remove-OrReport -Path $_.FullName -Reason "temporary snapshot staging directory"
            $deletedCount += 1
        }
}

foreach ($run in $allRuns) {
    $status = Read-ManifestStatus -RunRoot $run.FullName
    if ($status -eq "running") {
        continue
    }

    if ($DeleteFailedEmptyRuns -and ($status -eq "failed") -and -not (Has-TradeLog -RunRoot $run.FullName)) {
        Remove-OrReport -Path $run.FullName -Reason "failed run without a non-empty paper_trade_log.csv"
        $deletedCount += 1
        continue
    }

    if ($protected -contains $run.FullName) {
        continue
    }

    if ($run.LastWriteTime -lt $cutoff) {
        Remove-OrReport -Path $run.FullName -Reason "older than $DeleteOlderThanDays days and outside latest $KeepLatestRuns runs"
        $deletedCount += 1
    }
}

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry-run only. Re-run with -Apply to delete the listed paths."
}
Write-Host "Cleanup candidates: $deletedCount"

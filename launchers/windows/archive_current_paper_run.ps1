param(
    [string]$DriveRoot = "",
    [string]$RunId = "",
    [string]$OutputPath = ""
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

    $envRoot = [Environment]::GetEnvironmentVariable("PENNY_STOCK_PAPER_24H_DRIVE_ROOT")
    if (-not [string]::IsNullOrWhiteSpace($envRoot)) {
        return $envRoot
    }

    if ($env:OneDrive) {
        return (Join-Path $env:OneDrive "Penny_Stock_Runs")
    }
    if ($env:USERPROFILE) {
        return (Join-Path $env:USERPROFILE "Penny_Stock_Runs")
    }
    return (Join-Path $RootDir "sample_outputs\paper_24h_drive")
}

function Copy-IfExists {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (Test-Path $Source) {
        Copy-Item -Path $Source -Destination $Destination -Recurse -Force
    }
}

function Copy-DatabaseSnapshot {
    param(
        [string]$RunRoot,
        [string]$StagingRoot
    )

    $runDatabaseDir = Join-Path $RunRoot "database"
    $stagedDatabaseDir = Join-Path $StagingRoot "database"
    if (Test-Path $runDatabaseDir) {
        Copy-Item -Path $runDatabaseDir -Destination $stagedDatabaseDir -Recurse -Force
        return
    }

    $manifestPath = Join-Path $RunRoot "launcher_manifest.json"
    if (-not (Test-Path $manifestPath)) {
        return
    }

    try {
        $manifest = Get-Content -Path $manifestPath -Raw | ConvertFrom-Json
        $databasePath = [string]$manifest.database_path
        if ([string]::IsNullOrWhiteSpace($databasePath) -or -not (Test-Path $databasePath)) {
            return
        }

        New-Item -ItemType Directory -Path $stagedDatabaseDir -Force | Out-Null
        Copy-Item -Path $databasePath -Destination (Join-Path $stagedDatabaseDir (Split-Path -Leaf $databasePath)) -Force
        foreach ($suffix in @("-wal", "-shm")) {
            $sidecarPath = "$databasePath$suffix"
            if (Test-Path $sidecarPath) {
                Copy-Item -Path $sidecarPath -Destination (Join-Path $stagedDatabaseDir (Split-Path -Leaf $sidecarPath)) -Force
            }
        }
    }
    catch {
        Write-Host "WARNING: database snapshot was skipped: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

$DriveRoot = Resolve-DriveRoot -Value $DriveRoot
$RunsRoot = Join-Path $DriveRoot "paper_24h_runs"
if (-not (Test-Path $RunsRoot)) {
    throw "Run root does not exist: $RunsRoot"
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $latestRun = Get-ChildItem -Path $RunsRoot -Directory |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latestRun) {
        throw "No paper run folders found under: $RunsRoot"
    }
    $RunId = $latestRun.Name
}

$RunRoot = Join-Path $RunsRoot $RunId
if (-not (Test-Path $RunRoot)) {
    throw "Run folder does not exist: $RunRoot"
}

$ArchiveDir = Join-Path $RunRoot "archives"
if (-not (Test-Path $ArchiveDir)) {
    New-Item -ItemType Directory -Path $ArchiveDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $ArchiveDir "$RunId-snapshot-$timestamp.zip"
}

$staging = Join-Path $ArchiveDir ".snapshot_staging_$timestamp"
if (Test-Path $staging) {
    Remove-Item -Path $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging -Force | Out-Null

try {
    Copy-IfExists -Source (Join-Path $RunRoot "paper_trading") -Destination (Join-Path $staging "paper_trading")
    Copy-IfExists -Source (Join-Path $RunRoot "logs") -Destination (Join-Path $staging "logs")
    Copy-IfExists -Source (Join-Path $RunRoot "pipeline_outputs") -Destination (Join-Path $staging "pipeline_outputs")
    Copy-DatabaseSnapshot -RunRoot $RunRoot -StagingRoot $staging
    Copy-IfExists -Source (Join-Path $RunRoot "launcher_manifest.json") -Destination (Join-Path $staging "launcher_manifest.json")

    $snapshotManifest = [ordered]@{
        run_id = $RunId
        snapshot_created_at = (Get-Date).ToString("o")
        run_root = $RunRoot
        source_drive_root = $DriveRoot
        output_path = $OutputPath
        contents = @("paper_trading", "logs", "pipeline_outputs", "database", "launcher_manifest.json")
    }
    $snapshotManifest | ConvertTo-Json -Depth 4 |
        Set-Content -Path (Join-Path $staging "snapshot_manifest.json") -Encoding UTF8

    if (Test-Path $OutputPath) {
        Remove-Item -Path $OutputPath -Force
    }
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $OutputPath -Force
}
finally {
    if (Test-Path $staging) {
        Remove-Item -Path $staging -Recurse -Force
    }
}

Write-Host "Paper run snapshot archive written to:"
Write-Host "  $OutputPath"
Write-Host ""
Write-Host "Source run:"
Write-Host "  $RunRoot"

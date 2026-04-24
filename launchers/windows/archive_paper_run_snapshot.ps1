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

    $result = [ordered]@{
        included = $false
        source = $null
        files = @()
        warnings = @()
    }
    $runDatabaseDir = Join-Path $RunRoot "database"
    $stagedDatabaseDir = Join-Path $StagingRoot "database"
    if (Test-Path $runDatabaseDir) {
        Copy-Item -Path $runDatabaseDir -Destination $stagedDatabaseDir -Recurse -Force
        $stagedFiles = @(
            Get-ChildItem -Path $stagedDatabaseDir -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '\.(sqlite3|sqlite|db)(-wal|-shm)?$' }
        )
        if ($stagedFiles.Count -gt 0) {
            $result.included = $true
            $result.source = $runDatabaseDir
            $result.files = @($stagedFiles | ForEach-Object { $_.FullName })
            return $result
        }
        $result.warnings += "run database directory existed but contained no sqlite database files"
    }

    $manifestPath = Join-Path $RunRoot "launcher_manifest.json"
    if (-not (Test-Path $manifestPath)) {
        $result.warnings += "launcher_manifest.json was missing; database source path could not be resolved"
        return $result
    }

    try {
        $manifest = Get-Content -Path $manifestPath -Raw | ConvertFrom-Json
        $databasePath = [string]$manifest.database_path
        if ([string]::IsNullOrWhiteSpace($databasePath) -or -not (Test-Path $databasePath)) {
            $result.warnings += "launcher_manifest database_path was missing or not found"
            return $result
        }

        New-Item -ItemType Directory -Path $stagedDatabaseDir -Force | Out-Null
        Copy-Item -Path $databasePath -Destination (Join-Path $stagedDatabaseDir (Split-Path -Leaf $databasePath)) -Force
        $result.files += (Join-Path $stagedDatabaseDir (Split-Path -Leaf $databasePath))
        foreach ($suffix in @("-wal", "-shm")) {
            $sidecarPath = "$databasePath$suffix"
            if (Test-Path $sidecarPath) {
                Copy-Item -Path $sidecarPath -Destination (Join-Path $stagedDatabaseDir (Split-Path -Leaf $sidecarPath)) -Force
                $result.files += (Join-Path $stagedDatabaseDir (Split-Path -Leaf $sidecarPath))
            }
        }
        $result.included = $true
        $result.source = $databasePath
        return $result
    }
    catch {
        $result.warnings += "database snapshot was skipped: $($_.Exception.Message)"
        return $result
    }
}

$DriveRoot = Resolve-DriveRoot -Value $DriveRoot
$RunsRoots = @(
    (Join-Path $DriveRoot "paper_runs"),
    (Join-Path $DriveRoot "paper_24h_runs")
)
$existingRunsRoots = @($RunsRoots | Where-Object { Test-Path $_ })
if ($existingRunsRoots.Count -eq 0) {
    throw "No paper run roots exist under: $DriveRoot"
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $latestRun = $existingRunsRoots |
        ForEach-Object { Get-ChildItem -Path $_ -Directory } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latestRun) {
        throw "No paper run folders found under: $DriveRoot"
    }
    $RunId = $latestRun.Name
    $RunRoot = $latestRun.FullName
}
else {
    $RunRoot = $null
    foreach ($runsRoot in $existingRunsRoots) {
        $candidate = Join-Path $runsRoot $RunId
        if (Test-Path $candidate) {
            $RunRoot = $candidate
            break
        }
    }
    if ([string]::IsNullOrWhiteSpace($RunRoot)) {
        throw "Run folder does not exist for run id '$RunId' under: $DriveRoot"
    }
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
    $databaseSnapshot = Copy-DatabaseSnapshot -RunRoot $RunRoot -StagingRoot $staging
    Copy-IfExists -Source (Join-Path $RunRoot "launcher_manifest.json") -Destination (Join-Path $staging "launcher_manifest.json")
    foreach ($warning in @($databaseSnapshot.warnings)) {
        Write-Host "WARNING: $warning" -ForegroundColor Yellow
    }
    $contents = @("paper_trading", "logs", "pipeline_outputs", "launcher_manifest.json")
    if ($databaseSnapshot.included) {
        $contents += "database"
    }

    $snapshotManifest = [ordered]@{
        run_id = $RunId
        snapshot_created_at = (Get-Date).ToString("o")
        run_root = $RunRoot
        source_drive_root = $DriveRoot
        output_path = $OutputPath
        contents = $contents
        database = $databaseSnapshot
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

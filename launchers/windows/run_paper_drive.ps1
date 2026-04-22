param(
    [string]$DriveRoot = "",
    [string]$RunId = "",
    [int]$MaxRuntimeSeconds = 0,
    [int]$CheckIntervalSeconds = 0,
    [string]$Phase = "",
    [switch]$SkipInitialPipeline,
    [switch]$SkipPremktPredictor,
    [switch]$UseDefaultDatabase
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $RootDir
. (Join-Path $RootDir "scripts\windows_common.ps1")

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

    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add("G:\My Drive\Penny_Stock")
    $candidates.Add("G:\내 드라이브\Penny_Stock")
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE "Google Drive\My Drive\Penny_Stock"))
        $candidates.Add((Join-Path $env:USERPROFILE "OneDrive\Penny_Stock_Runs"))
        $candidates.Add((Join-Path $env:USERPROFILE "OneDrive\Penny_Stock"))
    }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
        $parent = Split-Path -Parent $candidate
        if ($parent -and (Test-Path $parent)) {
            return $candidate
        }
    }

    if ($env:USERPROFILE) {
        return (Join-Path $env:USERPROFILE "Penny_Stock_Paper_Runs")
    }
    return (Join-Path $RootDir "sample_outputs\paper_drive")
}

function Resolve-PaperRunSetting {
    param(
        [string]$Value,
        [string]$EnvName,
        [string]$LegacyEnvName,
        [string]$DefaultValue
    )

    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return $Value
    }
    $envValue = [Environment]::GetEnvironmentVariable($EnvName)
    if ([string]::IsNullOrWhiteSpace($envValue)) {
        $envValue = [Environment]::GetEnvironmentVariable($LegacyEnvName)
    }
    if (-not [string]::IsNullOrWhiteSpace($envValue)) {
        return $envValue
    }
    return $DefaultValue
}

function Resolve-PaperRunIntSetting {
    param(
        [int]$Value,
        [string]$EnvName,
        [string]$LegacyEnvName,
        [int]$DefaultValue
    )

    if ($Value -gt 0) {
        return $Value
    }
    foreach ($candidateName in @($EnvName, $LegacyEnvName)) {
        $envValue = [Environment]::GetEnvironmentVariable($candidateName)
        if (-not [string]::IsNullOrWhiteSpace($envValue)) {
            $parsed = 0
            if ([int]::TryParse($envValue, [ref]$parsed) -and $parsed -gt 0) {
                return $parsed
            }
        }
    }
    return $DefaultValue
}

function Invoke-LoggedNative {
    param(
        [string]$StepName,
        [scriptblock]$Command,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    Write-Host $StepName
    & $Command 1>> $StdoutPath 2>> $StderrPath
    $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    if ($exitCode -ne 0) {
        throw "$StepName failed with exit code $exitCode. See $StderrPath"
    }
    return $exitCode
}

function Write-LauncherManifest {
    $manifest = [ordered]@{
        run_id = $RunId
        started_at = $StartedAt
        ended_at = $script:EndedAt
        status = $script:RunStatus
        error = $script:RunError
        archive_error = $script:ArchiveError
        root_dir = $RootDir
        drive_root = $DriveRoot
        run_root = $RunRoot
        export_dir = $ExportDir
        log_dir = $LogDir
        pipeline_output_dir = $PipelineDir
        archive_path = $ArchivePath
        database_path = $DatabasePath
        database_copy_path = $DatabaseCopyPath
        use_default_database = [bool]$UseDefaultDatabase
        max_runtime_seconds = $MaxRuntimeSeconds
        check_interval_seconds = $CheckIntervalSeconds
        phase = $Phase
        skip_initial_pipeline = [bool]$SkipInitialPipeline
        skip_premkt_predictor = [bool]$SkipPremktPredictor
        init_db_exit_code = $script:InitDbExitCode
        initial_pipeline_exit_code = $script:InitialPipelineExitCode
        premkt_predictor_exit_code = $script:PremktPredictorExitCode
        trader_exit_code = $script:TraderExitCode
        archive_exit_code = $script:ArchiveExitCode
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $ManifestPath -Encoding UTF8
}

$DriveRoot = Resolve-DriveRoot -Value $DriveRoot
$RunId = Resolve-PaperRunSetting `
    -Value $RunId `
    -EnvName "PENNY_STOCK_PAPER_RUN_ID" `
    -LegacyEnvName "PENNY_STOCK_PAPER_24H_RUN_ID" `
    -DefaultValue ("paper_run_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$RunId = ($RunId -replace '[\\/:*?"<>|]', "_")
if ([string]::IsNullOrWhiteSpace($RunId)) {
    $RunId = "paper_run_" + (Get-Date -Format "yyyyMMdd_HHmmss")
}
$MaxRuntimeSeconds = Resolve-PaperRunIntSetting `
    -Value $MaxRuntimeSeconds `
    -EnvName "PENNY_STOCK_PAPER_MAX_RUNTIME_SECONDS" `
    -LegacyEnvName "PENNY_STOCK_PAPER_24H_MAX_RUNTIME_SECONDS" `
    -DefaultValue 0
$CheckIntervalSeconds = Resolve-PaperRunIntSetting `
    -Value $CheckIntervalSeconds `
    -EnvName "PENNY_STOCK_PAPER_CHECK_INTERVAL_SECONDS" `
    -LegacyEnvName "PENNY_STOCK_PAPER_24H_CHECK_INTERVAL_SECONDS" `
    -DefaultValue 60
$Phase = Resolve-PaperRunSetting `
    -Value $Phase `
    -EnvName "PENNY_STOCK_PAPER_PHASE" `
    -LegacyEnvName "PENNY_STOCK_PAPER_24H_PHASE" `
    -DefaultValue "auto"

$RunRoot = Join-Path (Join-Path $DriveRoot "paper_runs") $RunId
$ExportDir = Join-Path $RunRoot "paper_trading"
$LogDir = Join-Path $RunRoot "logs"
$PipelineDir = Join-Path $RunRoot "pipeline_outputs"
$ArchiveDir = Join-Path $RunRoot "archives"
$ArchivePath = Join-Path $ArchiveDir "$RunId-paper-performance.zip"
$ManifestPath = Join-Path $RunRoot "launcher_manifest.json"
$LocalRunRoot = Join-Path (Join-Path $RootDir "data\paper_runs") $RunId
if ($UseDefaultDatabase) {
    $DatabasePath = Join-Path $RootDir "data\penny_stock_radar.sqlite3"
}
else {
    $DatabasePath = Join-Path $LocalRunRoot "penny_stock_radar.sqlite3"
}
$DatabaseCopyDir = Join-Path $RunRoot "database"
$DatabaseCopyPath = Join-Path $DatabaseCopyDir "penny_stock_radar.sqlite3"

foreach ($path in @($RunRoot, $ExportDir, $LogDir, $PipelineDir, $ArchiveDir, $DatabaseCopyDir, (Split-Path -Parent $DatabasePath))) {
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

$script:InitDbExitCode = $null
$script:InitialPipelineExitCode = $null
$script:PremktPredictorExitCode = $null
$script:TraderExitCode = $null
$script:ArchiveExitCode = $null
$script:RunError = $null
$script:ArchiveError = $null
$script:EndedAt = $null
$script:RunStatus = "running"
$StartedAt = (Get-Date).ToString("o")

$env:PENNY_STOCK_DB_PATH = $DatabasePath
$env:PENNY_STOCK_PAPER_TRADE_DIR = $ExportDir
$env:PENNY_STOCK_LIVE_OBSERVABILITY_PATH = Join-Path $LogDir "live_metrics.jsonl"
$env:PENNY_STOCK_PAPER_RUN_ID = $RunId
$env:PENNY_STOCK_PAPER_24H_RUN_ID = $RunId
$env:PENNY_STOCK_RESET_DB = "0"

Write-LauncherManifest

Write-Host "========================================"
Write-Host " Penny Stock Radar Paper Drive Run"
Write-Host "========================================"
Write-Host ""
Write-Host "Run id:"
Write-Host "  $RunId"
Write-Host "Run root:"
Write-Host "  $RunRoot"
Write-Host "Paper exports:"
Write-Host "  $ExportDir"
Write-Host "Logs:"
Write-Host "  $LogDir"
Write-Host "Pipeline outputs:"
Write-Host "  $PipelineDir"
Write-Host "Archive:"
Write-Host "  $ArchivePath"
Write-Host "Database:"
Write-Host "  $DatabasePath"
Write-Host ""

$runtime = $null

try {
    $runtime = Ensure-PennyStockRuntime -RootDir $RootDir
    Initialize-PythonPathEnv -RootDir $RootDir

    $script:InitDbExitCode = Invoke-LoggedNative `
        -StepName "[1/4] Initializing database" `
        -StdoutPath (Join-Path $LogDir "init_db_stdout.log") `
        -StderrPath (Join-Path $LogDir "init_db_stderr.log") `
        -Command { & $runtime.VenvPython -m penny_stock_radar init-db }

    if ($SkipInitialPipeline) {
        Write-Host "[2/4] Skipping initial pipeline"
    }
    else {
        $pipelineStdout = Join-Path $LogDir "initial_pipeline_stdout.log"
        $pipelineStderr = Join-Path $LogDir "initial_pipeline_stderr.log"
        $universeMaxSymbols = if ($env:PENNY_STOCK_UNIVERSE_MAX_SYMBOLS) {
            $env:PENNY_STOCK_UNIVERSE_MAX_SYMBOLS
        }
        else {
            "250"
        }

        $null = Invoke-LoggedNative `
            -StepName "[2/4] Initial pipeline: building universe" `
            -StdoutPath $pipelineStdout `
            -StderrPath $pipelineStderr `
            -Command {
                & $runtime.VenvPython -m penny_stock_radar build-universe `
                    --max-symbols $universeMaxSymbols `
                    --export-json (Join-Path $PipelineDir "universe_candidates.sample.json")
            }
        $null = Invoke-LoggedNative `
            -StepName "[2/4] Initial pipeline: building watchlist" `
            -StdoutPath $pipelineStdout `
            -StderrPath $pipelineStderr `
            -Command {
                & $runtime.VenvPython -m penny_stock_radar build-watchlist `
                    --limit 10 `
                    --lookback-hours 48
            }
        $null = Invoke-LoggedNative `
            -StepName "[2/4] Initial pipeline: running replay pipeline" `
            -StdoutPath $pipelineStdout `
            -StderrPath $pipelineStderr `
            -Command {
                & $runtime.VenvPython -m penny_stock_radar run-replay-pipeline `
                    --output-csv (Join-Path $PipelineDir "mock_replay.sample.csv") `
                    --export-json (Join-Path $PipelineDir "replay_report.sample.json")
            }
        $null = Invoke-LoggedNative `
            -StepName "[2/4] Initial pipeline: exporting summary" `
            -StdoutPath $pipelineStdout `
            -StderrPath $pipelineStderr `
            -Command {
                & $runtime.VenvPython -m penny_stock_radar export-summary `
                    --json-output (Join-Path $PipelineDir "radar_summary.json") `
                    --markdown-output (Join-Path $PipelineDir "radar_summary.md")
            }
        $script:InitialPipelineExitCode = 0
    }

    if ($SkipPremktPredictor) {
        Write-Host "[3/4] Skipping premarket predictor"
    }
    else {
        $script:PremktPredictorExitCode = Invoke-LoggedNative `
            -StepName "[3/4] Running premarket predictor" `
            -StdoutPath (Join-Path $LogDir "premkt_predictor_stdout.log") `
            -StderrPath (Join-Path $LogDir "premkt_predictor_stderr.log") `
            -Command { & $runtime.VenvPython -m penny_stock_radar run-premkt-predictor }
    }

    if ($MaxRuntimeSeconds -gt 0) {
        Write-Host "[4/4] Starting paper trader for $MaxRuntimeSeconds seconds"
        & $runtime.VenvPython -m penny_stock_radar paper-trader `
            --check-interval-seconds $CheckIntervalSeconds `
            --phase $Phase `
            --max-runtime-seconds $MaxRuntimeSeconds `
            1>> (Join-Path $LogDir "paper_trader_stdout.log") `
            2>> (Join-Path $LogDir "paper_trader_stderr.log")
    }
    else {
        Write-Host "[4/4] Starting paper trader until stopped"
        & $runtime.VenvPython -m penny_stock_radar paper-trader `
            --check-interval-seconds $CheckIntervalSeconds `
            --phase $Phase `
            1>> (Join-Path $LogDir "paper_trader_stdout.log") `
            2>> (Join-Path $LogDir "paper_trader_stderr.log")
    }
    $script:TraderExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    if ($script:TraderExitCode -ne 0) {
        throw "Paper trader failed with exit code $script:TraderExitCode. See $(Join-Path $LogDir 'paper_trader_stderr.log')"
    }

    $script:RunStatus = "completed"
}
catch {
    $script:RunStatus = "failed"
    $script:RunError = $_.Exception.Message
    Write-Host ""
    Write-Host "ERROR: $script:RunError" -ForegroundColor Red
}
finally {
    $script:EndedAt = (Get-Date).ToString("o")
    try {
        if (Test-Path $DatabasePath) {
            Copy-Item -Path $DatabasePath -Destination $DatabaseCopyPath -Force
        }
        if ($null -ne $runtime) {
            Write-Host "Archiving paper performance artifacts..."
            & $runtime.VenvPython -m penny_stock_radar archive-paper-performance `
                --export-dir $ExportDir `
                --output-path $ArchivePath `
                --allow-fail `
                1>> (Join-Path $LogDir "archive_stdout.log") `
                2>> (Join-Path $LogDir "archive_stderr.log")
            $script:ArchiveExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
            if ($script:ArchiveExitCode -ne 0) {
                $script:ArchiveError = "Archive command failed with exit code $script:ArchiveExitCode."
                if ($script:RunStatus -eq "completed") {
                    $script:RunStatus = "archive_failed"
                }
            }
        }
        else {
            $script:ArchiveError = "Runtime was not initialized; archive command was skipped."
            $script:ArchiveExitCode = 1
            if ($script:RunStatus -eq "completed") {
                $script:RunStatus = "archive_failed"
            }
        }
    }
    catch {
        $script:ArchiveError = $_.Exception.Message
        $script:ArchiveExitCode = 1
        if ($script:RunStatus -eq "completed") {
            $script:RunStatus = "archive_failed"
        }
    }

    Write-LauncherManifest
}

Write-Host ""
Write-Host "Run manifest:"
Write-Host "  $ManifestPath"
Write-Host "Mac review after syncing/unzipping the archive:"
Write-Host "  ./scripts/psradar review-paper-performance --export-dir <unzipped>/paper_trading"
Write-Host ""

if ($script:RunError) {
    exit 1
}
if (($null -ne $script:ArchiveExitCode) -and ($script:ArchiveExitCode -ne 0)) {
    exit $script:ArchiveExitCode
}
exit 0

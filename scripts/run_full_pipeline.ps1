$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptsDir
Set-Location $RootDir

function Get-RunnerPython {
    $venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return @($venvPython)
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @("py", "-3")
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }
    throw "Python 3 is not installed."
}

function Invoke-PythonCommand {
    param(
        [string[]]$CommandParts,
        [string[]]$Arguments
    )

    if ($CommandParts.Length -gt 1) {
        & $CommandParts[0] @($CommandParts[1..($CommandParts.Length - 1)]) @Arguments
    }
    else {
        & $CommandParts[0] @Arguments
    }
}

function Assert-LastExitCode {
    param(
        [string]$StepName
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Step {
    param(
        [string]$Label,
        [string[]]$CommandParts,
        [string[]]$Arguments
    )

    Write-Host $Label
    Invoke-PythonCommand -CommandParts $CommandParts -Arguments $Arguments
    Assert-LastExitCode $Label
}

$runnerPython = Get-RunnerPython
$dbPath = Join-Path $RootDir "data\penny_stock_radar.sqlite3"

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RootDir\src;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = "$RootDir\src"
}

if ($env:PENNY_STOCK_RESET_DB -eq "1" -and (Test-Path $dbPath)) {
    Write-Host "[0/5] Resetting database because PENNY_STOCK_RESET_DB=1"
    Remove-Item $dbPath -Force
}

Invoke-Step -Label "[1/5] Initializing database" -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "init-db")
Invoke-Step -Label "[2/5] Building universe" -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "build-universe", "--max-symbols", "40", "--export-json", (Join-Path $RootDir "sample_outputs\universe_candidates.sample.json"))
Invoke-Step -Label "[3/5] Building watchlist" -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "build-watchlist", "--limit", "10", "--lookback-hours", "48")
Invoke-Step -Label "[4/5] Running replay pipeline" -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "run-replay-pipeline", "--output-csv", (Join-Path $RootDir "sample_outputs\mock_replay.sample.csv"), "--export-json", (Join-Path $RootDir "sample_outputs\replay_report.sample.json"))
Write-Host "Skipping sample social analysis. Run .\scripts\psradar analyze-social --mentions-csv <real_social_mentions.csv> when you have real mention data."
Invoke-Step -Label "[5/5] Exporting summary" -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "export-summary", "--json-output", (Join-Path $RootDir "sample_outputs\radar_summary.json"), "--markdown-output", (Join-Path $RootDir "sample_outputs\radar_summary.md"))
Write-Host "Pipeline completed successfully."

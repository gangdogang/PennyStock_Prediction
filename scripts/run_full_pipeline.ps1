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

$runnerPython = Get-RunnerPython
$dbPath = Join-Path $RootDir "data\penny_stock_radar.sqlite3"

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RootDir\src;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = "$RootDir\src"
}

if (Test-Path $dbPath) {
    Remove-Item $dbPath -Force
}

Invoke-PythonCommand -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "init-db")
Invoke-PythonCommand -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "build-universe", "--max-symbols", "40", "--export-json", (Join-Path $RootDir "sample_outputs\universe_candidates.sample.json"))
Invoke-PythonCommand -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "build-watchlist", "--limit", "10", "--lookback-hours", "48")
Invoke-PythonCommand -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "run-replay-pipeline", "--output-csv", (Join-Path $RootDir "sample_outputs\mock_replay.sample.csv"), "--export-json", (Join-Path $RootDir "sample_outputs\replay_report.sample.json"))
Write-Host "Skipping sample social analysis. Run .\scripts\psradar analyze-social --mentions-csv <real_social_mentions.csv> when you have real mention data."
Invoke-PythonCommand -CommandParts $runnerPython -Arguments @("-m", "penny_stock_radar", "export-summary", "--json-output", (Join-Path $RootDir "sample_outputs\radar_summary.json"), "--markdown-output", (Join-Path $RootDir "sample_outputs\radar_summary.md"))

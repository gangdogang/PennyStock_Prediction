$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location $RootDir

Write-Host "========================================"
Write-Host " Penny Stock Radar One-Click Launcher"
Write-Host "========================================"
Write-Host ""

function Get-BasePython {
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

$basePython = Get-BasePython
$venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$venvPip = Join-Path $RootDir ".venv\Scripts\pip.exe"
$dbPath = Join-Path $RootDir "data\penny_stock_radar.sqlite3"

if (-not (Test-Path $venvPython)) {
    Write-Host "[1/6] Creating virtual environment (.venv)..."
    Invoke-PythonCommand -CommandParts $basePython -Arguments @("-m", "venv", ".venv")
}

Write-Host "[2/6] Checking dependencies..."
$dependencyCheck = @"
import importlib.util
import sys

required = ["streamlit", "typer", "pandas", "pydantic", "httpx", "yfinance"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
"@

$dependenciesReady = $false
try {
    & $venvPython -c $dependencyCheck *> $null
    $dependenciesReady = $LASTEXITCODE -eq 0
}
catch {
    $dependenciesReady = $false
}

if (-not $dependenciesReady) {
    Write-Host "Installing required packages. This can take 1-3 minutes on the first run."
    & $venvPip install -U pip
    & $venvPip install -e ".[dev,ui]"
}
else {
    Write-Host "Dependencies are already installed. Skipping package install."
}

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Write-Host "[3/6] Creating .env from .env.example..."
    Copy-Item ".env.example" ".env"
}

Write-Host "[4/6] Checking whether a full refresh is needed..."
$needsRefresh = $true
if (Test-Path $dbPath) {
    $refreshCheck = @"
from datetime import datetime, timezone
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
row = connection.execute("SELECT created_at FROM scan_runs ORDER BY created_at DESC LIMIT 1").fetchone()
connection.close()

if row is None or not row[0]:
    raise SystemExit(1)

created_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
age_minutes = (datetime.now(timezone.utc) - created_at).total_seconds() / 60
raise SystemExit(0 if age_minutes <= 15 else 1)
"@
    $refreshIsFresh = $false
    try {
        & $venvPython -c $refreshCheck $dbPath *> $null
        $refreshIsFresh = $LASTEXITCODE -eq 0
    }
    catch {
        $refreshIsFresh = $false
    }
    if ($refreshIsFresh) {
        $needsRefresh = $false
    }
}

if ($needsRefresh) {
    Write-Host "Running the full pipeline. This can take a little while."
    & (Join-Path $RootDir "scripts\run_full_pipeline.ps1")
    if (($null -ne $LASTEXITCODE) -and ($LASTEXITCODE -ne 0)) {
        throw "Full pipeline failed with exit code $LASTEXITCODE."
    }
}
else {
    Write-Host "Recent data found in the last 15 minutes. Skipping startup refresh."
    Write-Host "Use the sidebar button '전체 최신화 실행' when you want a full recalculation."
}

Write-Host "[5/6] Launching dashboard..."
Write-Host ""
Write-Host "If the browser does not open automatically, visit:"
Write-Host "http://localhost:8501"
Write-Host ""
Write-Host "Press Ctrl+C in this terminal window to stop the server."
Write-Host ""

Write-Host "[6/6] Starting Streamlit"
Start-Process "http://localhost:8501"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RootDir\src;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = "$RootDir\src"
}
& $venvPython -m streamlit run (Join-Path $RootDir "src\penny_stock_radar\ui\app.py") --server.address localhost --server.port 8501

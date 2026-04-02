function Assert-LastExitCode {
    param(
        [string]$StepName
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE."
    }
}

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

function Ensure-EnvFile {
    param(
        [string]$RootDir
    )

    $envPath = Join-Path $RootDir ".env"
    $examplePath = Join-Path $RootDir ".env.example"

    if (-not (Test-Path $envPath) -and (Test-Path $examplePath)) {
        Write-Host "[3/3] Creating .env from .env.example..."
        Copy-Item $examplePath $envPath
    }
    elseif (Test-Path $envPath) {
        Write-Host "[3/3] Environment file is ready."
    }
    else {
        throw ".env.example was not found, so .env could not be created."
    }

    return $envPath
}

function Ensure-PennyStockRuntime {
    param(
        [string]$RootDir,
        [switch]$IncludeUi
    )

    $basePython = Get-BasePython
    $venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
    $venvPip = Join-Path $RootDir ".venv\Scripts\pip.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Host "[1/3] Creating virtual environment (.venv)..."
        Invoke-PythonCommand -CommandParts $basePython -Arguments @("-m", "venv", ".venv")
        Assert-LastExitCode "Creating virtual environment"
    }

    Write-Host "[2/3] Checking dependencies..."
    $required = @("typer", "pandas", "pydantic", "pydantic_settings", "httpx", "rich", "yfinance")
    if ($IncludeUi) {
        $required += "streamlit"
    }
    $requiredLiteral = ($required | ForEach-Object { "'$_'" }) -join ", "
    $dependencyCheck = @"
import importlib.util
import sys

required = [$requiredLiteral]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
"@

    & $venvPython -c $dependencyCheck *> $null
    if ($LASTEXITCODE -ne 0) {
        $packageSpec = if ($IncludeUi) { ".[dev,ui]" } else { ".[dev]" }
        Write-Host "Installing required packages. This can take 1-3 minutes on the first run."
        & $venvPip install -U pip
        Assert-LastExitCode "Upgrading pip"
        & $venvPip install -e $packageSpec
        Assert-LastExitCode "Installing project dependencies"
    }
    else {
        Write-Host "Dependencies are already installed. Skipping package install."
    }

    $envPath = Ensure-EnvFile -RootDir $RootDir

    return [pscustomobject]@{
        VenvPython = $venvPython
        VenvPip = $venvPip
        EnvPath = $envPath
    }
}

function Initialize-PythonPathEnv {
    param(
        [string]$RootDir
    )

    $srcDir = Join-Path $RootDir "src"
    if ($env:PYTHONPATH) {
        $env:PYTHONPATH = "$srcDir;$env:PYTHONPATH"
    }
    else {
        $env:PYTHONPATH = $srcDir
    }
}

function Invoke-DashboardRefreshIfNeeded {
    param(
        [string]$RootDir,
        [string]$VenvPython
    )

    $dbPath = Join-Path $RootDir "data\penny_stock_radar.sqlite3"
    $needsRefresh = $true

    Write-Host "[4/5] Checking whether a full refresh is needed..."
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
        & $VenvPython -c $refreshCheck $dbPath *> $null
        if ($LASTEXITCODE -eq 0) {
            $needsRefresh = $false
        }
    }

    if ($needsRefresh) {
        Write-Host "Running the full pipeline. This can take a little while."
        & (Join-Path $RootDir "scripts\run_full_pipeline.ps1")
        Assert-LastExitCode "Running the full pipeline"
    }
    else {
        Write-Host "Recent data found in the last 15 minutes. Skipping startup refresh."
        Write-Host "Use the sidebar button '전체 최신화 실행' when you want a full recalculation."
    }
}

function Get-LanIPv4Addresses {
    $addresses = @()

    if (Get-Command Get-NetIPAddress -ErrorAction SilentlyContinue) {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -ne "127.0.0.1" -and
                $_.IPAddress -notlike "169.254.*"
            } |
            Select-Object -ExpandProperty IPAddress -Unique
    }

    if (-not $addresses) {
        $addresses = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object {
                $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
                $_.IPAddressToString -ne "127.0.0.1" -and
                $_.IPAddressToString -notlike "169.254.*"
            } |
            ForEach-Object { $_.IPAddressToString } |
            Select-Object -Unique
    }

    return @($addresses)
}

function Rotate-LogFile {
    param(
        [string]$Path,
        [int64]$MaxBytes = 1048576,
        [int]$KeepCount = 5
    )

    if (-not (Test-Path $Path)) {
        return
    }

    $file = Get-Item $Path -ErrorAction SilentlyContinue
    if (-not $file -or $file.Length -lt $MaxBytes) {
        return
    }

    for ($index = $KeepCount; $index -ge 1; $index--) {
        $olderPath = "$Path.$index"
        $newerPath = if ($index -eq 1) { $Path } else { "$Path." + ($index - 1) }
        if (Test-Path $olderPath) {
            Remove-Item $olderPath -Force
        }
        if (Test-Path $newerPath) {
            Move-Item $newerPath $olderPath -Force
        }
    }
}

function Get-AiSupervisorTaskName {
    return "Penny Stock Radar Supervisor"
}

function Get-AiSupervisorLogPaths {
    param(
        [string]$RootDir
    )

    $logDir = Join-Path $RootDir "automation\logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    return [pscustomobject]@{
        LogDir = $logDir
        Stdout = Join-Path $logDir "ai_supervisor_windows_stdout.log"
        Stderr = Join-Path $logDir "ai_supervisor_windows_stderr.log"
    }
}

function Get-AutomationStatusPath {
    param(
        [string]$RootDir
    )

    $stateDir = Join-Path $RootDir "automation\state"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    return Join-Path $stateDir "automation_status.json"
}

function Get-PaperTraderTaskName {
    return "Penny Stock Radar Paper Trader"
}

function Get-PaperTraderLogPaths {
    param(
        [string]$RootDir
    )

    $logDir = Join-Path $RootDir "automation\logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    return [pscustomobject]@{
        LogDir = $logDir
        Stdout = Join-Path $logDir "paper_trader_windows_stdout.log"
        Stderr = Join-Path $logDir "paper_trader_windows_stderr.log"
    }
}

function Get-CurrentWindowsUserId {
    if ($env:USERDOMAIN) {
        return "$($env:USERDOMAIN)\$($env:USERNAME)"
    }
    return $env:USERNAME
}

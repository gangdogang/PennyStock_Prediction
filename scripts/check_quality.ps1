param(
    [switch]$StrictCoverageGate
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonBin = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

$env:PYTHONPATH = if ($env:PYTHONPATH) {
    "$ProjectRoot\src;$env:PYTHONPATH"
} else {
    "$ProjectRoot\src"
}

Push-Location $ProjectRoot
try {
    & $PythonBin -m pytest tests/test_regression_golden.py -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $PythonBin -m pytest tests/
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $coverageArgs = @("-m", "penny_stock_radar.quality_gates")
    if ($StrictCoverageGate) {
        $coverageArgs += "--strict-coverage-gate"
    }
    & $PythonBin @coverageArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

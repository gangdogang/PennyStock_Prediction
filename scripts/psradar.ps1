param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Resolve-Path (Join-Path $ScriptDir "..")
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $PythonBin = $VenvPython
} else {
    $PythonBin = "python"
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RootDir\src;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$RootDir\src"
}

& $PythonBin -m penny_stock_radar @Arguments
exit $LASTEXITCODE

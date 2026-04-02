$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    Set-Location $RootDir
    . (Join-Path $RootDir "scripts\windows_common.ps1")

    $taskName = Get-PaperTraderTaskName
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

    Write-Host "========================================"
    Write-Host " Penny Stock Radar Paper Trader"
    Write-Host " Scheduled Task Remove"
    Write-Host "========================================"
    Write-Host ""

    if (-not $task) {
        Write-Host "Scheduled task is not installed."
        return
    }

    if ($task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $taskName
    }

    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

    Write-Host "Scheduled task removed."
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close..." | Out-Null
}

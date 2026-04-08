$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
    Set-Location $RootDir
    . (Join-Path $RootDir "scripts\windows_common.ps1")

    $taskName = Get-AiSupervisorTaskName
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $logs = Get-AiSupervisorLogPaths -RootDir $RootDir
    $statusPath = Get-AutomationStatusPath -RootDir $RootDir

    Write-Host "========================================"
    Write-Host " Penny Stock Radar Supervisor"
    Write-Host " Scheduled Task Status"
    Write-Host "========================================"
    Write-Host ""

    if (-not $task) {
        Write-Host "Scheduled task is not installed."
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $taskName

    Write-Host "Task name:"
    Write-Host "  $taskName"
    Write-Host ""
    Write-Host "Status:"
    Write-Host "  State           : $($task.State)"
    Write-Host "  Last run time   : $($info.LastRunTime)"
    Write-Host "  Last task result: $($info.LastTaskResult)"
    Write-Host "  Next run time   : $($info.NextRunTime)"
    Write-Host ""

    Write-Host "Automation status JSON:"
    Write-Host "  $statusPath"
    if (Test-Path $statusPath) {
        Get-Content $statusPath
    }
    else {
        Write-Host "No status file yet."
    }

    Write-Host ""
    if (Test-Path $logs.Stdout) {
        Write-Host "Recent stdout:"
        Get-Content $logs.Stdout -Tail 10
    }
    else {
        Write-Host "No stdout log yet."
    }

    Write-Host ""
    if (Test-Path $logs.Stderr) {
        Write-Host "Recent stderr:"
        Get-Content $logs.Stderr -Tail 10
    }
    else {
        Write-Host "No stderr log yet."
    }

    Write-Host ""
    Write-Host "Outputs:"
    Write-Host "  review -> $(Join-Path $RootDir 'automation\inbox\gemini_review.md')"
    Write-Host "  html   -> $(Join-Path $RootDir 'sample_outputs\radar_dashboard.html')"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close..." | Out-Null
}

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

try {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RootDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
    Set-Location $RootDir
    . (Join-Path $RootDir "scripts\windows_common.ps1")

    Write-Host "========================================"
    Write-Host " Penny Stock Radar Paper Trader"
    Write-Host " Scheduled Task Install"
    Write-Host "========================================"
    Write-Host ""

    $runtime = Ensure-PennyStockRuntime -RootDir $RootDir
    $logs = Get-PaperTraderLogPaths -RootDir $RootDir
    $taskName = Get-PaperTraderTaskName
    $backgroundScript = Join-Path $RootDir "launchers\windows\run_paper_trader_background.ps1"
    $userId = Get-CurrentWindowsUserId

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $backgroundScript) `
        -WorkingDirectory $RootDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Runs Penny Stock Radar paper trader in the background at user logon."

    Write-Host "Installing or updating the scheduled task..."
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 2

    $info = Get-ScheduledTaskInfo -TaskName $taskName

    Write-Host ""
    Write-Host "Scheduled task is ready."
    Write-Host ""
    Write-Host "Task name:"
    Write-Host "  $taskName"
    Write-Host ""
    Write-Host "Current status:"
    Write-Host "  State           : $((Get-ScheduledTask -TaskName $taskName).State)"
    Write-Host "  Last run time   : $($info.LastRunTime)"
    Write-Host "  Last task result: $($info.LastTaskResult)"
    Write-Host ""
    Write-Host "Logs:"
    Write-Host "  stdout -> $($logs.Stdout)"
    Write-Host "  stderr -> $($logs.Stderr)"
    Write-Host "  csv    -> $(Join-Path $RootDir 'sample_outputs\paper_trading')"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close..." | Out-Null
}

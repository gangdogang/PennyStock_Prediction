$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir
. (Join-Path $RootDir "scripts\windows_common.ps1")

Write-Host "========================================"
Write-Host " Penny Stock Radar Supervisor"
Write-Host " Scheduled Task Install"
Write-Host "========================================"
Write-Host ""

$runtime = Ensure-PennyStockRuntime -RootDir $RootDir
$logs = Get-AiSupervisorLogPaths -RootDir $RootDir
$taskName = Get-AiSupervisorTaskName
$backgroundScript = Join-Path $RootDir "run_ai_supervisor_background.ps1"
$userId = Get-CurrentWindowsUserId
$statusPath = Get-AutomationStatusPath -RootDir $RootDir

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $backgroundScript) `
    -WorkingDirectory $RootDir
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$repeatAnchor = (Get-Date).AddMinutes(1)
$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At $repeatAnchor `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel LeastPrivilege
$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($logonTrigger, $repeatTrigger) `
    -Settings $settings `
    -Principal $principal `
    -Description "Runs Penny Stock Radar AI supervisor in the background at logon and every 15 minutes."

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
Write-Host "  Next run time   : $($info.NextRunTime)"
Write-Host ""
Write-Host "Logs and status:"
Write-Host "  stdout -> $($logs.Stdout)"
Write-Host "  stderr -> $($logs.Stderr)"
Write-Host "  status -> $statusPath"
Write-Host "  review -> $(Join-Path $RootDir 'automation\inbox\gemini_review.md')"
Write-Host "  html   -> $(Join-Path $RootDir 'sample_outputs\radar_dashboard.html')"

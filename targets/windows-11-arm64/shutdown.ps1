# Return to Packer before cleanup removes the build credentials.
# SYSTEM can finish cleanup even when the build user's WinRM session ends.
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ImageFactory\cleanup.ps1'
# An immediate task can invalidate WinRM before Packer receives the exit code.
# Packer starts its shutdown timeout only after this command has returned.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30)
Register-ScheduledTask -TaskName ImageFactory-Cleanup -Action $action -Trigger $trigger -User SYSTEM -RunLevel Highest -Force | Out-Null

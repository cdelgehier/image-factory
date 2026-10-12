# Return to Packer before cleanup changes credentials and removes listeners.
# SYSTEM can finish cleanup even when the build user's WinRM session ends.
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ImageFactory\cleanup.ps1'
Register-ScheduledTask -TaskName ImageFactory-Cleanup -Action $action -User SYSTEM -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName ImageFactory-Cleanup

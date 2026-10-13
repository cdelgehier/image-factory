# Generalize the final disk so clones receive separate Windows identities.
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName ImageFactory-Cleanup -Confirm:$false
& powercfg.exe /hibernate off
if ($LASTEXITCODE -ne 0) { throw 'Cannot disable hibernation' }
& dism.exe /Online /Cleanup-Image /StartComponentCleanup
if ($LASTEXITCODE -notin @(0, 3010)) { throw 'Windows component cleanup failed' }

# Keep a firmware-independent ARM64 boot path for fresh QEMU NVRAM on import.
& mountvol.exe S: /S
if ($LASTEXITCODE -ne 0) { throw 'Cannot mount the EFI partition' }
try {
    & bcdboot.exe C:\Windows /s S: /f UEFI
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare the EFI boot files' }
} finally {
    & mountvol.exe S: /D
}

# The temporary build password must not survive in SAM or setup/autologon files.
Set-LocalUser vagrant -Password (ConvertTo-SecureString 'vagrant' -AsPlainText -Force)
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $winlogon AutoAdminLogon '0'
Remove-ItemProperty $winlogon DefaultPassword -ErrorAction SilentlyContinue
# Answer files can contain the build password. Fail if one cannot be removed;
# other temporary files are only a space-saving cleanup.
Get-ChildItem C:\Windows\Panther,C:\Windows\System32\Sysprep\Panther -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object Name -Like '*unattend*' | Remove-Item -Force
Get-ChildItem Cert:\LocalMachine\My | Where-Object FriendlyName -EQ ImageFactory-WinRM | Remove-Item
Get-ChildItem WSMan:\localhost\Listener | Remove-Item -Recurse -Force
@('C:\Windows\Panther\*', 'C:\Windows\System32\Sysprep\Panther\*',
  'C:\Windows\Temp\*', 'C:\Users\vagrant\AppData\Local\Temp\*',
  'C:\Users\vagrant\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\*',
  'C:\ImageFactory\ready', 'C:\ImageFactory\lean.ps1') | ForEach-Object {
    Remove-Item $_ -Recurse -Force -ErrorAction SilentlyContinue
}

# QEMU detects these zero writes and reclaims deleted data without allocating
# the full guest disk on the host. Leave 512 MiB free for Sysprep and always
# remove the fill file, including after an I/O error.
$fill = 'C:\image-factory-zero.tmp'
$stream = [IO.File]::Open($fill, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $buffer = New-Object byte[] (4MB)
    $remaining = [IO.DriveInfo]::new('C:\').AvailableFreeSpace - 512MB
    while ($remaining -gt 0) {
        $count = [int][Math]::Min($buffer.Length, $remaining)
        $stream.Write($buffer, 0, $count)
        $remaining -= $count
    }
    $stream.Flush($true)
} finally {
    $stream.Dispose()
    Remove-Item $fill -Force
}
$sysprep = 'C:\Windows\System32\Sysprep\Sysprep.exe'
$process = Start-Process $sysprep -ArgumentList '/generalize', '/oobe', '/shutdown', '/quiet', '/unattend:C:\ImageFactory\Sysprep.xml' -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "Sysprep failed: $($process.ExitCode). Check C:\Windows\System32\Sysprep\Panther." }

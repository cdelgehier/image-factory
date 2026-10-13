# Keep desktop access authenticated and document exchange encrypted.
$ErrorActionPreference = 'Stop'
$rdp = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
Set-ItemProperty $rdp fDenyTSConnections 0
Set-ItemProperty "$rdp\WinStations\RDP-Tcp" UserAuthentication 1
Set-Service TermService -StartupType Automatic
Start-Service TermService

$directory = 'C:\LabData'
$account = "$env:COMPUTERNAME\vagrant"
New-Item -ItemType Directory -Path $directory -Force | Out-Null
# Use group SIDs so permissions do not depend on the guest's display language.
& icacls.exe $directory /inheritance:r /grant '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "${account}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Document folder permissions could not be set' }
Set-Service LanmanServer -StartupType Automatic
Start-Service LanmanServer
if (!(Get-SmbShare -Name LabData -ErrorAction SilentlyContinue)) {
    New-SmbShare -Name LabData -Path $directory -FullAccess $account -EncryptData $true -CachingMode None | Out-Null
}

foreach ($rule in @(@{Name='ImageFactory-RDP'; Port=3389}, @{Name='ImageFactory-Documents'; Port=445})) {
    if (!(Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue)) {
        # QEMU forwards host connections through its user-network gateway.
        New-NetFirewallRule -Name $rule.Name -DisplayName $rule.Name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $rule.Port -RemoteAddress 10.0.2.2 | Out-Null
    }
}

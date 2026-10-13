# Setup and each generalized clone need their own HTTPS certificate.
$ErrorActionPreference = 'Stop'
$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $winlogon AutoAdminLogon '0'
Remove-ItemProperty $winlogon DefaultPassword -ErrorAction SilentlyContinue
Set-LocalUser -Name vagrant -PasswordNeverExpires $true -ErrorAction SilentlyContinue

# Local admin remoting needs a full token. UAC remains enabled for desktop use.
$policies = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
New-ItemProperty $policies LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force | Out-Null
Set-Service WinRM -StartupType Automatic
Start-Service WinRM
# Create a local management listener before using the WSMan provider. Remove
# HTTP only after the HTTPS listener and service settings are ready.
Enable-PSRemoting -SkipNetworkProfileCheck -Force
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTPS' } | Remove-Item -Recurse -Force
$certificate = New-SelfSignedCertificate -DnsName $env:COMPUTERNAME -CertStoreLocation Cert:\LocalMachine\My -FriendlyName ImageFactory-WinRM
New-Item WSMan:\localhost\Listener -Transport HTTPS -Address '*' -CertificateThumbprint $certificate.Thumbprint -Force | Out-Null
Set-Item WSMan:\localhost\Service\Auth\Basic $true
# Writing an unchanged service setting can trigger the WSMan Public-network bug.
# Keep the secure default and fail if the service accepts unencrypted traffic.
$allowUnencrypted = 'WSMan:\localhost\Service\AllowUnencrypted'
if ((Get-Item $allowUnencrypted).Value -ne 'false') {
    Set-Item $allowUnencrypted -Value $false
}
if ((Get-Item $allowUnencrypted).Value -ne 'false') {
    throw 'WinRM must reject unencrypted traffic.'
}
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -contains 'Transport=HTTP' } | Remove-Item -Recurse -Force
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
if (!(Get-NetFirewallRule -Name ImageFactory-WinRM -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name ImageFactory-WinRM -DisplayName 'Image Factory WinRM HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5986 | Out-Null
}
# Windows Setup may still hold the password in its cached answer file.
Remove-Item C:\Windows\Panther\unattend.xml -Force -ErrorAction SilentlyContinue
New-Item -ItemType File -Path C:\ImageFactory\ready -Force | Out-Null

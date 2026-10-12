# Remove consumer apps from current users and future profiles. Keep servicing,
# Defender, the Store, Edge/WebView2 and common tools such as Calculator and Paint.
$ErrorActionPreference = 'Stop'
$apps = @(
    'Clipchamp.Clipchamp', 'Microsoft.BingNews', 'Microsoft.BingWeather',
    'Microsoft.GamingApp', 'Microsoft.GetHelp', 'Microsoft.Getstarted',
    'Microsoft.MicrosoftOfficeHub', 'Microsoft.MicrosoftSolitaireCollection',
    'Microsoft.OutlookForWindows', 'Microsoft.People', 'Microsoft.WindowsFeedbackHub',
    'Microsoft.YourPhone', 'Microsoft.ZuneMusic', 'Microsoft.ZuneVideo',
    'Microsoft.Xbox.TCUI', 'Microsoft.XboxApp', 'Microsoft.XboxGameOverlay',
    'Microsoft.XboxGamingOverlay', 'Microsoft.XboxIdentityProvider',
    'Microsoft.XboxSpeechToTextOverlay', 'MicrosoftTeams', 'MSTeams',
    'Microsoft.Copilot', 'MicrosoftCorporationII.MicrosoftFamily'
)
foreach ($name in $apps) {
    Get-AppxPackage -AllUsers -Name $name | Remove-AppxPackage -AllUsers
    Get-AppxProvisionedPackage -Online | Where-Object DisplayName -EQ $name | Remove-AppxProvisionedPackage -Online | Out-Null
}
# OneDrive is a desktop application and needs its own uninstaller.
Stop-Process -Name OneDrive -Force -ErrorAction SilentlyContinue
$installers = @('C:\Windows\System32\OneDriveSetup.exe', 'C:\Windows\SysWOW64\OneDriveSetup.exe',
    'C:\Program Files\Microsoft OneDrive\*\OneDriveSetup.exe',
    'C:\Users\vagrant\AppData\Local\Microsoft\OneDrive\*\OneDriveSetup.exe')
foreach ($path in $installers) {
    foreach ($installer in (Get-Item $path -ErrorAction SilentlyContinue)) {
        $arguments = @('/uninstall')
        if ($installer.FullName.StartsWith('C:\Program Files\', [StringComparison]::OrdinalIgnoreCase)) {
            $arguments += '/allusers'
        }
        $process = Start-Process $installer.FullName -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "OneDrive removal failed: $($process.ExitCode)" }
    }
}
# Prevent the default profile from installing OneDrive at its first login.
& reg.exe load HKU\ImageFactoryDefault C:\Users\Default\NTUSER.DAT | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot open the default user profile' }
try {
    & reg.exe delete 'HKU\ImageFactoryDefault\Software\Microsoft\Windows\CurrentVersion\Run' /v OneDriveSetup /f 2>$null | Out-Null
} finally {
    [GC]::Collect()
    & reg.exe unload HKU\ImageFactoryDefault | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot close the default user profile' }
}
# CompactOS reduces installed Windows files while keeping Windows Update usable.
& compact.exe /CompactOS:always
if ($LASTEXITCODE -ne 0) { throw 'CompactOS failed' }
New-Item -ItemType File -Path C:\ImageFactory\lean-ready -Force | Out-Null

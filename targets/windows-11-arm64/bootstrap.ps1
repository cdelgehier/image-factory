# Copy only signed ARM64 network drivers. NVMe storage uses a Windows driver.
$ErrorActionPreference = 'Stop'
$media = Split-Path -Parent $PSCommandPath
New-Item -ItemType Directory -Path C:\ImageFactory -Force | Out-Null
Copy-Item "$media\configure-winrm.ps1" C:\ImageFactory\configure-winrm.ps1
& pnputil.exe /add-driver "$media\Drivers\NetKVM\w11\ARM64\*.inf" /install
if ($LASTEXITCODE -notin @(0, 3010)) { throw "Driver installation failed: $LASTEXITCODE" }

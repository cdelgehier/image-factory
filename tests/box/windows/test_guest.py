"""Check the private Windows box through Vagrant's actual WinRM connection."""

from pathlib import Path

from conftest import powershell, vagrant


def test_guest_is_windows_11_pro_arm64(machines: Path) -> None:
    result = powershell(
        machines,
        "first",
        "@{os=(Get-CimInstance Win32_OperatingSystem).Caption; arch=$env:PROCESSOR_ARCHITECTURE; edition=(Get-ItemProperty 'HKLM:/SOFTWARE/Microsoft/Windows NT/CurrentVersion').EditionID} | ConvertTo-Json -Compress",
    )
    assert "Windows 11" in result["os"]
    assert result["arch"] == "ARM64"
    assert result["edition"] == "Professional"


def test_account_has_admin_access_and_https_winrm(machines: Path) -> None:
    result = powershell(
        machines,
        "first",
        "@{user=$env:USERNAME; admin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); winrm=(Get-Service WinRM).Status.ToString(); ready=(Test-Path C:/ImageFactory/ready)} | ConvertTo-Json -Compress",
    )
    assert result == {
        "user": "vagrant",
        "admin": True,
        "winrm": "Running",
        "ready": True,
    }


def test_vagrant_can_upload_a_file(machines: Path) -> None:
    result = powershell(
        machines,
        "first",
        "@{content=(Get-Content C:/ImageFactory/marker.txt -Raw).Trim()} | ConvertTo-Json -Compress",
    )
    assert result["content"] == "image-factory Windows upload test"


def test_winrm_rejects_plaintext_and_keeps_the_firewall_enabled(machines: Path) -> None:
    result = powershell(
        machines,
        "first",
        "$listeners = @(Get-ChildItem WSMan:/localhost/Listener); "
        "@{unencrypted=(Get-Item WSMan:/localhost/Service/AllowUnencrypted).Value; "
        "http=@($listeners | Where-Object { $_.Keys -contains 'Transport=HTTP' }).Count; "
        "https=@($listeners | Where-Object { $_.Keys -contains 'Transport=HTTPS' }).Count; "
        "disabled_firewalls=@(Get-NetFirewallProfile | Where-Object Enabled -EQ False).Count} "
        "| ConvertTo-Json -Compress",
    )
    assert result == {
        "unencrypted": "false",
        "http": 0,
        "https": 1,
        "disabled_firewalls": 0,
    }


def test_generalized_clones_have_separate_identities_and_certificates(
    machines: Path,
) -> None:
    command = "@{guid=(Get-ItemProperty HKLM:/SOFTWARE/Microsoft/Cryptography).MachineGuid; sid=(Get-LocalUser vagrant).SID.Value; certificate=(Get-ChildItem Cert:/LocalMachine/My | Where-Object FriendlyName -EQ ImageFactory-WinRM).Thumbprint; hostname=$env:COMPUTERNAME} | ConvertTo-Json -Compress"
    first = powershell(machines, "first", command)
    second = powershell(machines, "second", command)
    for name in ("guid", "sid", "certificate", "hostname"):
        assert first[name] and second[name]
        assert first[name] != second[name], name


def test_consumer_apps_are_removed_and_windows_can_still_be_serviced(
    machines: Path,
) -> None:
    command = "@{removed=@(Get-AppxProvisionedPackage -Online | Where-Object DisplayName -In @('Clipchamp.Clipchamp','Microsoft.GamingApp','Microsoft.Copilot','MSTeams')).Count; defender=(Get-MpComputerStatus).AntivirusEnabled; store=@(Get-AppxProvisionedPackage -Online | Where-Object DisplayName -EQ Microsoft.WindowsStore).Count; update=(Get-CimInstance Win32_Service -Filter \"Name='wuauserv'\").StartMode; zero=(Test-Path C:/image-factory-zero.tmp); autologon=(Get-ItemProperty 'HKLM:/SOFTWARE/Microsoft/Windows NT/CurrentVersion/Winlogon').AutoAdminLogon} | ConvertTo-Json -Compress"
    result = powershell(machines, "first", command)
    assert result["removed"] == 0
    assert result["defender"] is True
    assert result["store"] == 1
    assert result["update"] != "Disabled"
    assert result["zero"] is False
    assert result["autologon"] == "0"


def test_shutdown_and_restart_keep_the_machine_identity(machines: Path) -> None:
    command = "@{guid=(Get-ItemProperty HKLM:/SOFTWARE/Microsoft/Cryptography).MachineGuid} | ConvertTo-Json -Compress"
    before = powershell(machines, "first", command)
    vagrant(machines, "halt", "first")
    assert ",first,state,stopped" in vagrant(
        machines, "status", "first", "--machine-readable"
    )
    vagrant(machines, "up", "first", "--provider=qemu", "--no-provision")
    assert powershell(machines, "first", command) == before

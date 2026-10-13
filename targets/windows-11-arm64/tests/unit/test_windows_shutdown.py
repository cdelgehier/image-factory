"""Check cleanup scheduling and failure logs without changing the host system."""

import json
import os
import subprocess
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[2]


def test_cleanup_starts_after_the_packer_command_returns(tmp_path: Path) -> None:
    """Keep the build connection alive until Packer can wait for shutdown."""

    harness = tmp_path / "schedule.ps1"
    # ScheduledTasks is a Windows boundary. The fake records its schedule while
    # PowerShell still executes the real shutdown script and date calculation.
    harness.write_text(
        """
$ErrorActionPreference = 'Stop'
$before = Get-Date
$script:started = $false
function New-ScheduledTaskAction {
    param($Execute, $Argument)
    @{ Execute = $Execute; Argument = $Argument }
}
function New-ScheduledTaskTrigger {
    param([switch]$Once, $At)
    @{ At = $At }
}
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $User, $RunLevel, [switch]$Force)
    $script:registered = @{ Trigger = $Trigger; User = $User; RunLevel = $RunLevel }
}
function Start-ScheduledTask {
    param($TaskName)
    $script:started = $true
}
. $env:SHUTDOWN_SCRIPT
$delay = 0
if ($script:registered.Trigger) {
    $delay = ($script:registered.Trigger.At - $before).TotalSeconds
}
@{ Delay = $delay; Started = $script:started; User = $script:registered.User;
   RunLevel = $script:registered.RunLevel } | ConvertTo-Json -Compress
"""
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(harness)],
        env=os.environ | {"SHUTDOWN_SCRIPT": str(RECIPE / "shutdown.ps1")},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    schedule = json.loads(result.stdout)
    assert 15 <= schedule["Delay"] <= 60
    assert schedule["Started"] is False
    assert schedule["User"] == "SYSTEM"
    assert schedule["RunLevel"] == "Highest"


def test_cleanup_retains_a_log_when_a_guest_command_fails(tmp_path: Path) -> None:
    """Keep the reason for a scheduled failure after its console has closed."""

    directory = tmp_path / "ImageFactory"
    directory.mkdir()
    harness = tmp_path / "failure.ps1"
    harness.write_text(
        """
$ErrorActionPreference = 'Stop'
New-PSDrive -Name C -PSProvider FileSystem -Root $env:GUEST_ROOT | Out-Null
function Unregister-ScheduledTask {
    param($TaskName, [switch]$Confirm)
}
function powercfg.exe { throw 'Hibernation cleanup failed in the test guest' }
. $env:CLEANUP_SCRIPT
exit $LASTEXITCODE
"""
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(harness)],
        env=os.environ
        | {"GUEST_ROOT": str(tmp_path), "CLEANUP_SCRIPT": str(RECIPE / "cleanup.ps1")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    report = directory / "cleanup.log"
    assert report.is_file()
    assert "Hibernation cleanup failed in the test guest" in report.read_text()

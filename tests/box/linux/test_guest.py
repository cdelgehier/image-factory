import json
from pathlib import Path
from typing import Any, cast

import testinfra
from conftest import ROOT, TARGET, vagrant


def test_guest_account_and_required_packages(first_host: Any) -> None:
    expected = json.loads((TARGET.directory / "guest.json").read_text())
    assert first_host.system_info.distribution == expected["distribution"]
    assert first_host.file("/etc/os-release").contains(
        f'VERSION_ID="{expected["version"]}"'
    )
    assert first_host.run("uname -m").stdout.strip() == "aarch64"
    assert first_host.run("whoami").stdout.strip() == TARGET.ssh_username
    assert first_host.run("sudo -n true").rc == 0
    for package in expected["packages"]:
        assert first_host.package(package).is_installed
    for service in expected["services"]:
        assert first_host.service(service).is_running
    status = first_host.run("sudo cloud-init status --format json")
    assert status.rc == 0
    assert json.loads(status.stdout)["status"] == "done"


def test_removes_build_credentials_and_replaces_the_vagrant_key(
    first_host: Any,
) -> None:
    keys = first_host.file(
        f"/home/{TARGET.ssh_username}/.ssh/authorized_keys"
    ).content_string
    assert "image-factory-build" not in keys
    standard_key = (ROOT / "packer/vagrant.pub").read_text().split()[1]
    assert standard_key not in keys
    assert (
        first_host.run(
            f"sudo grep -R -l image-factory-build /var/lib/cloud /etc/cloud /home/{TARGET.ssh_username} /root 2>/dev/null"
        ).rc
        == 1
    )
    assert (
        first_host.run("sudo find /tmp /var/tmp -name '*image-factory*'").stdout.strip()
        == ""
    )
    assert not first_host.file("/image-factory-zero").exists
    assert set(
        first_host.run(
            "sudo sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '"
        ).stdout.splitlines()
    ) == {"permitrootlogin no", "passwordauthentication no"}


def test_synchronizes_files_with_rsync(machines: tuple[Path, dict[str, Any]]) -> None:
    directory, hosts = machines
    assert (
        hosts["first"].file("/vagrant/sync-marker.txt").content_string
        == "image-factory rsync test\n"
    )
    (directory / "sync-marker.txt").write_text("updated by the rsync test\n")
    vagrant(directory, "rsync", "first")
    assert (
        hosts["first"].file("/vagrant/sync-marker.txt").content_string
        == "updated by the rsync test\n"
    )


def test_clones_have_separate_identities_and_can_communicate(
    machines: tuple[Path, dict[str, Any]],
) -> None:
    _, hosts = machines
    first, second = hosts["first"], hosts["second"]
    identities = [
        host.file("/etc/machine-id").content_string.strip() for host in (first, second)
    ]
    assert all(len(value) == 32 and value != "0" * 32 for value in identities)
    assert identities[0] != identities[1]
    assert (
        first.file("/etc/ssh/ssh_host_ed25519_key.pub").content_string
        != second.file("/etc/ssh/ssh_host_ed25519_key.pub").content_string
    )
    for host, address in ((first, "192.168.105.52"), (second, "192.168.105.51")):
        ping = host.run(f"ping -c 3 -W 10 {address}")
        assert ping.rc == 0
        assert " 0% packet loss" in ping.stdout


def test_shutdown_and_restart_keep_the_machine_identity(
    machines: tuple[Path, dict[str, Any]],
) -> None:
    directory, hosts = machines
    host = hosts["second"]
    machine_id = host.file("/etc/machine-id").content_string
    boot_id = host.file("/proc/sys/kernel/random/boot_id").content_string
    key = host.file("/etc/ssh/ssh_host_ed25519_key.pub").content_string
    vagrant(directory, "halt", "second")
    assert ",second,state,stopped" in vagrant(
        directory, "status", "second", "--machine-readable"
    )
    vagrant(directory, "up", "second", "--provider=qemu", "--no-parallel")
    configuration = directory / "second.ssh-config"
    configuration.write_text(vagrant(directory, "ssh-config", "second"))
    # Testinfra loads its host modules at runtime.
    host = cast(Any, testinfra.get_host("ssh://second", ssh_config=str(configuration)))
    hosts["second"] = host
    assert host.run("sudo cloud-init status --wait").rc == 0
    assert host.file("/etc/machine-id").content_string == machine_id
    assert host.file("/etc/ssh/ssh_host_ed25519_key.pub").content_string == key
    assert host.file("/proc/sys/kernel/random/boot_id").content_string != boot_id

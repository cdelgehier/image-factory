"""Import two private Windows clones and keep logs even when boot fails."""

import json
import os
import socket
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from image_factory.targets import ROOT, load_target

TARGET = load_target()


def free_port() -> int:
    """Ask the host for a free endpoint instead of using a fixed test port."""

    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def vagrant(directory: Path, *arguments: str, timeout: int = 2400) -> str:
    """Keep CLI diagnostics and bound every operation on fixture-owned machines."""

    environment = os.environ | {
        "VAGRANT_DEFAULT_PROVIDER": "qemu",
        "VAGRANT_DOTFILE_PATH": str(directory / ".vagrant"),
        "VAGRANT_NO_COLOR": "1",
        "IMAGE_FACTORY_WINDOWS_PASSWORD": "vagrant",
    }
    with (directory / "vagrant.log").open("a") as log:
        log.write(f"vagrant {' '.join(arguments)}\n")
        try:
            result = subprocess.run(
                ["vagrant", *arguments],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            log.write(str(error))
            raise
        log.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"Vagrant failed; see {directory / 'vagrant.log'}")
        return result.stdout


def powershell(directory: Path, name: str, command: str) -> dict:
    """Return guest JSON so assertions do not depend on formatted CLI messages."""

    output = vagrant(directory, "winrm", name, "-c", command)
    for line in output.splitlines():
        if line.strip().startswith("{"):
            return json.loads(line.strip())
    raise ValueError("The guest did not return a JSON object")


@pytest.fixture(scope="session")
def machines() -> Iterator[Path]:
    """Exercise the final archive without changing the user's installed boxes."""

    directory = ROOT / "reports" / f"windows-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    box_name = f"image-factory-windows-test-{uuid.uuid4().hex[:8]}"
    # The provider also creates SSH forwarding, even for a WinRM guest.
    ports = set()
    while len(ports) < 4:
        ports.add(free_port())
    first_winrm, second_winrm, first_ssh, second_ssh = ports
    (directory / "marker.txt").write_text("image-factory Windows upload test\n")
    (directory / "Vagrantfile").write_text(f'''
Vagrant.configure("2") do |config|
  config.vm.box = "{box_name}"
  config.vm.box_architecture = "arm64"
  config.vm.box_check_update = false
  config.vm.provision "file", source: "marker.txt", destination: "C:/ImageFactory/marker.txt"
  [["first", {first_winrm}, {first_ssh}], ["second", {second_winrm}, {second_ssh}]].each do |name, winrm_port, ssh_port|
    config.vm.define name do |machine|
      machine.vm.network "forwarded_port", guest: 5986, host: winrm_port,
        host_ip: "127.0.0.1", id: "winrm-ssl", auto_correct: true
      machine.vm.provider "qemu" do |qemu|
        qemu.ssh_port = ssh_port
        qemu.ssh_auto_correct = true
      end
    end
  end
end
''')
    started = []
    try:
        checksum = TARGET.box.with_suffix(".box.sha256").read_text().split()[0]
        vagrant(
            directory,
            "box",
            "add",
            "--name",
            box_name,
            "--provider",
            "libvirt",
            "--architecture",
            "arm64",
            "--checksum-type",
            "sha256",
            "--checksum",
            checksum,
            str(TARGET.box),
        )
        for name in ("first", "second"):
            vagrant(directory, "up", name, "--provider=qemu", "--no-parallel")
            started.append(name)
        yield directory
    finally:
        for name in started:
            try:
                vagrant(
                    directory,
                    "winrm",
                    name,
                    "-c",
                    "Get-Content C:/ImageFactory/cleanup.log -ErrorAction SilentlyContinue; Get-Content C:/Windows/System32/Sysprep/Panther/setuperr.log -ErrorAction SilentlyContinue; Get-Service WinRM; Get-NetAdapter",
                    timeout=120,
                )
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                (directory / f"{name}-diagnostic.log").write_text(str(error))
        errors = []
        for arguments in (
            ("destroy", "--force"),
            ("box", "remove", box_name, "--provider", "libvirt", "--all"),
        ):
            try:
                vagrant(directory, *arguments, timeout=600)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
        if errors:
            pytest.fail("Test resource cleanup failed: " + "; ".join(errors))

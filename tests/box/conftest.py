"""Run two test-owned Vagrant clones and keep their diagnostic logs."""

import os
import secrets
import socket
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import testinfra

ROOT = Path(__file__).resolve().parents[2]


def free_port(start: int) -> int:
    """Find a loopback port for one QEMU network endpoint."""
    for _ in range(100):
        port = start + secrets.randbelow(9000)
        with socket.socket() as connection:
            try:
                connection.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free loopback port found")


def vagrant(directory: Path, *arguments: str, timeout: int = 3600) -> str:
    """Run Vagrant in the owned directory and append its output."""
    environment = os.environ | {
        "VAGRANT_DEFAULT_PROVIDER": "qemu",
        "VAGRANT_DOTFILE_PATH": str(directory / ".vagrant"),
        "VAGRANT_NO_COLOR": "1",
    }
    with (directory / "vagrant.log").open("a") as log:
        log.write(f"vagrant {' '.join(arguments)}\n")
        log.flush()
        process = subprocess.Popen(
            ["vagrant", *arguments],
            cwd=directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
            log.write(stdout)
            raise
        log.write(stdout)
        if process.returncode:
            raise RuntimeError(
                f"Vagrant failed: {' '.join(arguments)}. See {directory / 'vagrant.log'}"
            )
        return stdout


@pytest.fixture(scope="session")
def machines() -> Iterator[tuple[Path, dict[str, Any]]]:
    """Import the final box and boot the listener before the connector."""
    identifier = uuid.uuid4().hex[:8]
    directory = ROOT / "reports" / f"vagrant-{identifier}"
    directory.mkdir(parents=True)
    box_name = f"image-factory-test-{identifier}"
    stream_port, first_port, second_port = (
        free_port(40000),
        free_port(20000),
        free_port(30000),
    )
    (directory / "sync-marker.txt").write_text("image-factory rsync test\n")
    (directory / "Vagrantfile").write_text(f'''
Vagrant.configure("2") do |config|
  config.vm.box = "{box_name}"
  config.vm.box_architecture = "arm64"
  config.vm.box_check_update = false
  config.vm.boot_timeout = 2700
  config.vm.synced_folder ".", "/vagrant", type: "rsync", rsync__exclude: [".vagrant/", "*.log", "*.ssh-config"]
  [ ["first", "192.168.105.51", "listen=127.0.0.1:{stream_port}", {first_port}],
    ["second", "192.168.105.52", "connect=127.0.0.1:{stream_port}", {second_port}] ].each do |name, ip, socket, ssh_port|
    config.vm.define name do |machine|
      machine.vm.network "private_network", ip: ip
      machine.vm.usable_port_range = (ssh_port + 1)..(ssh_port + 10)
      machine.vm.provider "qemu" do |qemu|
        qemu.arch = "aarch64"
        qemu.machine = "virt,accel=tcg"
        qemu.cpu = "max"
        qemu.smp = "2"
        qemu.memory = "2G"
        qemu.ssh_port = ssh_port
        qemu.ssh_auto_correct = true
        qemu.advanced_network = true
        qemu.net_mode = :socket
        qemu.socket_opts = socket
        qemu.graceful_timeout = 180
        qemu.extra_qemu_args = ["-serial", "file:{directory}/#{{name}}-serial.log",
          "-device", "virtio-serial-pci", "-chardev", "socket,path={directory}/#{{name}}-agent.sock,server=on,wait=off,id=agent",
          "-device", "virtserialport,chardev=agent,name=org.qemu.guest_agent.0"]
      end
    end
  end
end
''')
    hosts: dict[str, Any] = {}
    try:
        checksum = (ROOT / "dist/debian-13-arm64.box.sha256").read_text().split()[0]
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
            str(ROOT / "dist/debian-13-arm64.box"),
        )
        for name in ("first", "second"):
            vagrant(directory, "up", name, "--provider=qemu", "--no-parallel")
            configuration = directory / f"{name}.ssh-config"
            configuration.write_text(vagrant(directory, "ssh-config", name))
            hosts[name] = testinfra.get_host(
                f"ssh://{name}", ssh_config=str(configuration)
            )
            assert hosts[name].run("sudo cloud-init status --wait").rc == 0
        yield directory, hosts
    finally:
        for name, host in hosts.items():
            try:
                diagnostic = host.run(
                    "sudo journalctl -b --no-pager; sudo cat /var/log/cloud-init*.log; ip addr"
                )
                (directory / f"{name}-guest.log").write_text(
                    diagnostic.stdout + diagnostic.stderr
                )
            except Exception as error:
                (directory / f"{name}-diagnostic.log").write_text(str(error))
        cleanup_errors = []
        for arguments in (
            ("destroy", "--force"),
            ("box", "remove", box_name, "--provider", "libvirt", "--all"),
        ):
            try:
                vagrant(directory, *arguments, timeout=600)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            pytest.fail("Test resource cleanup failed: " + "; ".join(cleanup_errors))


@pytest.fixture
def first_host(machines: tuple[Path, dict[str, Any]]) -> Any:
    """Expose the first clone over SSH."""
    return machines[1]["first"]

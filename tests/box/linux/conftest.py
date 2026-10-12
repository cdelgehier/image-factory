"""Run consumer tests with temporary Vagrant machines and keep failure logs.

Each run owns its box name and machine directory. Cleanup can therefore remove
test resources without changing existing Vagrant machines or installed boxes.
"""

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

from image_factory.targets import ROOT, load_target

TARGET = load_target()


def free_port(start: int) -> int:
    """Choose an unused loopback port within one test endpoint's range.

    Random choices reduce clashes between test runs sharing a runner. The
    listener and SSH endpoints use separate ranges to avoid choosing each other.
    """

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
    """Run Vagrant with test-owned state and keep its output before raising.

    Guest boot can fail before an SSH connection exists. CLI logs remain
    available in that case, and a timeout prevents the fixture from waiting forever.
    """

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
            # Drain the stopped CLI so its final output is kept for diagnostics.
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
    """Import the final box and provide two clones to the shared Linux tests.

    Tests use the packaged box, including its Vagrantfile and SSH account,
    rather than the standalone build disk. The fixture keeps logs and attempts
    cleanup even if import, boot or a test assertion fails.
    """

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
    # TCG matches GitHub runners; HVF examples are checked on physical Apple Silicon.
    (directory / "Vagrantfile").write_text(f'''
Vagrant.configure("2") do |config|
  config.vm.box = "{box_name}"
  config.vm.box_architecture = "{TARGET.architecture}"
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
        checksum = TARGET.box.with_suffix(".box.sha256").read_text().split()[0]
        vagrant(
            directory,
            "box",
            "add",
            "--name",
            box_name,
            "--provider",
            TARGET.box_provider,
            "--architecture",
            TARGET.architecture,
            "--checksum-type",
            "sha256",
            "--checksum",
            checksum,
            str(TARGET.box),
        )
        # The socket listener must be running before the connector can boot.
        for name in ("first", "second"):
            vagrant(directory, "up", name, "--provider=qemu", "--no-parallel")
            configuration = directory / f"{name}.ssh-config"
            configuration.write_text(vagrant(directory, "ssh-config", name))
            hosts[name] = testinfra.get_host(
                f"ssh://{name}", ssh_config=str(configuration)
            )
            # SSH can be ready before cloud-init finishes guest setup.
            assert hosts[name].run("sudo cloud-init status --wait").rc == 0
        yield directory, hosts
    finally:
        # Collect guest evidence before destroy removes access to the machines.
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
        # Attempt box removal even if destroying a machine fails.
        for arguments in (
            ("destroy", "--force"),
            ("box", "remove", box_name, "--provider", TARGET.box_provider, "--all"),
        ):
            try:
                vagrant(directory, *arguments, timeout=600)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                cleanup_errors.append(str(error))
        if cleanup_errors:
            pytest.fail("Test resource cleanup failed: " + "; ".join(cleanup_errors))


@pytest.fixture
def first_host(machines: tuple[Path, dict[str, Any]]) -> Any:
    """Reuse the first fixture-owned clone for checks that need only one guest."""

    return machines[1]["first"]

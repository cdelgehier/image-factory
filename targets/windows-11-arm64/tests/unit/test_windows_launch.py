"""Check VM startup without Microsoft media or a Windows installation."""

import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest

RECIPE = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine() != "arm64",
    reason="The Windows recipe uses HVF on Apple Silicon",
)
def test_packer_launches_arm64_uefi_without_windows_media(tmp_path: Path) -> None:
    """Exercise the real launch arguments without building a Windows image."""

    for tool in ("packer", "qemu-system-aarch64", "qemu-img", "brew"):
        if shutil.which(tool) is None:
            pytest.skip(f"The launch check requires {tool}")
    prefix = subprocess.check_output(["brew", "--prefix", "qemu"], text=True).strip()
    firmware = Path(prefix) / "share/qemu/edk2-aarch64-code.fd"
    recipe = tmp_path / "targets/windows-11-arm64"
    recipe.mkdir(parents=True)
    source, _ = (RECIPE / "build.pkr.hcl").read_text().split("\nbuild {", 1)
    # Keep the recipe's devices and firmware settings; use a small, empty VM.
    settings = {
        "communicator": '"none"',
        "cpus": "1",
        "memory": "512",
        "disk_size": '"16M"',
        "disk_compression": "false",
        "boot_wait": '"1s"',
        "boot_command": "[]",
        "shutdown_command": '""',
        "shutdown_timeout": '"1s"',
    }
    for name, value in settings.items():
        source = re.sub(rf"(?m)^  {name}\s*=.*$", f"  {name} = {value}", source)
    template = recipe / "build.pkr.hcl"
    # Provisioners and packaging need Windows; this check stops after VM startup.
    template.write_text(source + '\nbuild { sources = ["source.qemu.windows"] }\n')
    media = tmp_path / "empty.iso"
    media.write_bytes(bytes(4096))
    variables = tmp_path / "source.pkrvars.json"
    variables.write_text(
        json.dumps(
            {
                "source_url": "https://www.microsoft.com/windows.iso",
                "source_build": "launch-check",
                "source_checksum": "sha256:"
                + hashlib.sha256(media.read_bytes()).hexdigest(),
                "iso_path": str(media),
                "seed_iso": str(media),
                "firmware": str(firmware),
                "firmware_vars": str(firmware.with_name("edk2-arm-vars.fd")),
                "build_password": "Launch-check-1234",
            }
        )
    )
    environment = os.environ.copy()
    environment.pop("PACKER_LOG", None)
    environment.pop("PACKER_LOG_PATH", None)
    # A user's source variables must not change the isolated launch check.
    for name in list(environment):
        if name.startswith("PKR_VAR_"):
            del environment[name]
    subprocess.run(
        ["packer", "init", str(template)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    log_path = tmp_path / "packer-launch.log"
    with (
        log_path.open("w") as log,
        subprocess.Popen(
            [
                "packer",
                "build",
                "-color=false",
                "-on-error=cleanup",
                f"-var-file={variables}",
                str(template),
            ],
            cwd=tmp_path,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process,
    ):
        try:
            process.wait(timeout=30)
        finally:
            # Stop only this test's process group, including QEMU after a timeout.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    output = log_path.read_text()
    # Empty media cannot shut itself down; reaching this step proves VM startup.
    assert "Waiting for shutdown..." in output, output
    assert "Error launching VM" not in output, output
    assert "Failed to shutdown" in output, output
    assert not (tmp_path / "dist/windows-11-arm64/disk").exists()

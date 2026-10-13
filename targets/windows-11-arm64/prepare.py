"""Build private Windows media without saving temporary passwords in the repo.

The Windows ISO comes from the user. Only the pinned UTM driver media is
fetched automatically. Generated answer files are removed even after failure.
"""

import os
import platform
import secrets
import shutil
import signal
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TextIO
from urllib.request import urlopen
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field

from image_factory.artifacts import sha256
from image_factory.sources import Source
from image_factory.targets import load_target


class DriverSource(BaseModel):
    """Require a fixed upstream release and checksum before downloading tools."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(
        pattern=r"^https://github\.com/utmapp/qemu/releases/download/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+\.iso$"
    )
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def verify_iso(path: Path, checksum: str) -> None:
    """Require the pinned media before Windows Setup can erase a VM disk."""

    if not path.is_file():
        raise FileNotFoundError(f"ISO file not found: {path}")
    actual = f"sha256:{sha256(path)}"
    if checksum != actual:
        raise ValueError(
            f"The ISO checksum does not match the source pin: {path}\n"
            f"Expected: {checksum}\nActual: {actual}"
        )


def render_answer(template: Path, password: str) -> str:
    """Escape a temporary password as XML data, never as a shell command."""

    if len(password) < 12 or any(ord(character) < 32 for character in password):
        raise ValueError("The build password must contain at least 12 safe characters")
    return template.read_text().replace("BUILD_PASSWORD", escape(password))


def driver_media(recipe: Path, cache: Path) -> Path:
    """Cache verified driver media; reject an altered download or cache entry."""

    pin = DriverSource.model_validate_json((recipe / "drivers.json").read_text())
    checksum = pin.sha256
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"utm-{checksum}.iso"
    if not path.exists():
        # An interrupted download must not replace a previously verified file.
        with tempfile.NamedTemporaryFile(dir=cache) as temporary:
            with urlopen(pin.url, timeout=60) as response:
                shutil.copyfileobj(response, temporary)
            temporary.flush()
            verify_iso(Path(temporary.name), f"sha256:{checksum}")
            shutil.copyfile(temporary.name, path)
    verify_iso(path, f"sha256:{checksum}")
    return path


def record_progress(output: str, log: TextIO, password: str) -> None:
    """Show each build message and save the same text without private credentials."""

    public = output.replace(password, "<sensitive>")
    # Flush both destinations so a stalled VM still has readable diagnostics.
    log.write(public)
    log.flush()
    print(public, end="", flush=True)


def run_packer(
    command: list[str],
    *,
    root: Path,
    environment: dict[str, str],
    log_path: Path,
    password: str,
) -> None:
    """Stream Packer output and allow it to clean up its VM after Ctrl+C."""

    with (
        log_path.open("w") as log,
        subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Own a separate group so cancellation cannot stop other lab VMs.
            start_new_session=True,
        ) as process,
    ):
        assert process.stdout is not None
        try:
            for line in process.stdout:
                record_progress(line, log, password)
            returncode = process.wait()
        except KeyboardInterrupt:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGINT)
            try:
                output, _ = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                # A stuck plugin must not leave this build's QEMU running.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
            record_progress(output, log, password)
            raise
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)


def build() -> None:
    """Prepare private setup media and run Packer on Apple Silicon.

    The temporary password travels through Packer's environment. It never
    appears in command arguments, the build manifest or the bundled Vagrantfile.
    """

    target = load_target()
    if target.name != "windows-11-arm64":
        raise ValueError("Select the Windows recipe before building")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This Windows recipe requires macOS on Apple Silicon")
    if os.environ.get("PACKER_LOG") not in (None, "", "0"):
        raise ValueError(
            "Disable PACKER_LOG to keep temporary credentials out of debug logs"
        )
    iso_value = os.environ.get("WINDOWS_ISO")
    if not iso_value:
        raise ValueError("Set WINDOWS_ISO to the pinned Microsoft ARM64 ISO")
    iso = Path(iso_value).expanduser().resolve()
    # QEMU drive options use commas as separators even when arguments are quoted.
    if "," in str(iso) or "," in str(target.root):
        raise ValueError("Media and repository paths must not contain commas")
    source = Source.model_validate_json(
        (target.directory / "source.pkrvars.json").read_text()
    )
    verify_iso(iso, source.source_checksum)
    if target.disk.parent.exists() or target.box.exists():
        raise ValueError(f"Build output already exists in {target.dist}")
    target.dist.mkdir(parents=True, exist_ok=True)
    reports = target.root / "reports"
    reports.mkdir(exist_ok=True)
    workspace = target.root / ".build"
    workspace.mkdir(exist_ok=True)
    drivers = driver_media(target.directory, target.root / "packer_cache")
    qemu_prefix = subprocess.check_output(
        ["brew", "--prefix", "qemu"], text=True
    ).strip()
    firmware = Path(qemu_prefix) / "share/qemu/edk2-aarch64-code.fd"
    with tempfile.TemporaryDirectory(prefix="windows-", dir=workspace) as directory:
        private = Path(directory)
        seed = private / "seed"
        seed.mkdir()
        password = "Build-" + secrets.token_urlsafe(24)
        (seed / "Autounattend.xml").write_text(
            render_answer(target.directory / "Autounattend.xml", password)
        )
        (seed / "image-factory-media").touch()
        for name in ("bootstrap.ps1", "configure-winrm.ps1"):
            shutil.copyfile(target.directory / name, seed / name)
        # UTM uses hard links across Windows versions, so tar needs the full ISO.
        with tempfile.TemporaryDirectory(prefix="drivers-", dir=private) as driver_dir:
            subprocess.run(["tar", "-xf", str(drivers), "-C", driver_dir], check=True)
            # Copy only ARM64 network files; Windows already has an NVMe driver.
            network = Path("Drivers/NetKVM/w11/ARM64")
            shutil.copytree(Path(driver_dir) / network, seed / network)
        seed_iso = private / "seed.iso"
        subprocess.run(
            [
                "hdiutil",
                "makehybrid",
                "-iso",
                "-joliet",
                "-o",
                str(seed_iso),
                str(seed),
            ],
            check=True,
        )
        seed_iso.chmod(0o600)
        variables = private / "vars.fd"
        shutil.copyfile(firmware.with_name("edk2-arm-vars.fd"), variables)
        environment = os.environ | {
            "PKR_VAR_iso_path": str(iso),
            "PKR_VAR_seed_iso": str(seed_iso),
            "PKR_VAR_firmware": str(firmware),
            "PKR_VAR_firmware_vars": str(variables),
            "PKR_VAR_build_password": password,
        }
        template = str(target.directory / "build.pkr.hcl")
        subprocess.run(["packer", "init", template], check=True)
        run_packer(
            [
                "packer",
                "build",
                "-color=false",
                "-on-error=cleanup",
                f"-var-file={target.directory / 'source.pkrvars.json'}",
                template,
            ],
            root=target.root,
            environment=environment,
            log_path=reports / f"{target.name}-packer.log",
            password=password,
        )


if __name__ == "__main__":
    try:
        build()
    except (FileNotFoundError, ValueError) as error:
        # Input errors need an actionable message; unexpected errors keep a traceback.
        raise SystemExit(str(error)) from error

"""Build private Windows media without saving temporary passwords in the repo.

The Windows ISO comes from the user. Only the pinned UTM driver media is
fetched automatically. Generated answer files are removed even after failure.
"""

import os
import platform
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
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
        # Windows has an inbox NVMe driver; only the VirtIO network driver is needed.
        subprocess.run(
            ["tar", "-xf", str(drivers), "-C", str(seed), "Drivers/NetKVM/w11/ARM64"],
            check=True,
        )
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
        with (reports / f"{target.name}-packer.log").open("w") as log:
            subprocess.run(
                [
                    "packer",
                    "build",
                    "-color=false",
                    "-on-error=cleanup",
                    f"-var-file={target.directory / 'source.pkrvars.json'}",
                    template,
                ],
                cwd=target.root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )


if __name__ == "__main__":
    try:
        build()
    except (FileNotFoundError, ValueError) as error:
        # Input errors need an actionable message; unexpected errors keep a traceback.
        raise SystemExit(str(error)) from error

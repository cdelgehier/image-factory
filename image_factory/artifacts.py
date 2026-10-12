"""Check the final box and record its source, checksum and tools."""

import hashlib
import json
import shutil
import subprocess
import tarfile as tar
import tempfile
from pathlib import Path
from typing import Self

from image_factory.catalogue import BOX_FILE, validate_asset
from image_factory.sources import CONFIG, Source


class PackerTarInfo(tar.TarInfo):
    """Read GNU access times without treating them as a path prefix."""

    @classmethod
    def fromtarfile(cls, tarfile: tar.TarFile) -> Self:
        """Use the name field for the GNU headers written by Go."""
        archive = tarfile
        offset = archive.fileobj.tell()
        header = archive.fileobj.read(512)
        archive.fileobj.seek(offset)
        member = super().fromtarfile(archive)
        if header[257:265] == tar.GNU_MAGIC and header[156:157] in (
            tar.REGTYPE,
            tar.AREGTYPE,
        ):
            member.name = (
                header[:100].split(b"\0", 1)[0].decode(archive.encoding, archive.errors)
            )
        return member


def sha256(path: Path) -> str:
    """Hash a file without loading the disk image into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack_box(box: Path, destination: Path) -> Path:
    """Read only the three expected regular files from Packer."""
    destination.mkdir(parents=True, exist_ok=True)
    with tar.open(box, tarinfo=PackerTarInfo) as archive:
        members = archive.getmembers()
        expected = {"box_0.img", "metadata.json", "Vagrantfile"}
        if (
            len(members) != 3
            or {item.name for item in members} != expected
            or any(not item.isfile() for item in members)
        ):
            raise ValueError("Unexpected box archive contents")
        for member in members:
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Missing archive member")
            with stream, (destination / member.name).open("wb") as target:
                shutil.copyfileobj(stream, target)
    metadata = json.loads((destination / "metadata.json").read_text())
    if (
        metadata.get("provider") != "libvirt"
        or metadata.get("architecture") != "arm64"
        or metadata.get("disks") != [{"path": "box_0.img", "format": "qcow2"}]
    ):
        raise ValueError("Invalid provider, architecture or disk metadata")
    vagrantfile = (destination / "Vagrantfile").read_text()
    if (
        'config.ssh.username = "debian"' not in vagrantfile
        or "config.ssh.insert_key = true" not in vagrantfile
        or "PRIVATE KEY" in vagrantfile
        or "image-factory-build" in vagrantfile
    ):
        raise ValueError("Invalid SSH configuration in the box")
    return destination / "box_0.img"


def check_disk(path: Path) -> dict:
    """Require a healthy, standalone QCOW2 image."""
    info = json.loads(
        subprocess.check_output(
            ["qemu-img", "info", "--output=json", str(path)], text=True
        )
    )
    if info.get("format") != "qcow2" or info.get("backing-filename"):
        raise ValueError("The disk must be a standalone QCOW2 image")
    subprocess.run(["qemu-img", "check", str(path)], check=True)
    return info


def main() -> None:
    """Write a checksum and manifest after validating both outputs."""
    box = Path("dist") / BOX_FILE
    validate_asset(box.stat().st_size)
    source = Source.model_validate_json(CONFIG.read_text())
    disk = Path("dist/disk/debian-13-arm64.qcow2")
    info = check_disk(disk)
    with tempfile.TemporaryDirectory(prefix="image-factory-archive-") as directory:
        packaged = unpack_box(box, Path(directory))
        check_disk(packaged)
        if sha256(packaged) != sha256(disk):
            raise ValueError("The box does not contain the final standalone disk")
    checksum = sha256(box)
    (Path("dist") / f"{BOX_FILE}.sha256").write_text(f"{checksum}  {BOX_FILE}\n")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    manifest = {
        "source": source.model_dump(),
        "ssh_username": "debian",
        "architecture": "arm64",
        "accelerator": "tcg",
        "cpu": "max",
        "virtual_size": info["virtual-size"],
        "commit": revision.stdout.strip() or "uncommitted",
        "box": {"filename": BOX_FILE, "sha256": checksum, "bytes": box.stat().st_size},
        "disk_sha256": sha256(disk),
        "tools": {
            "packer": subprocess.check_output(
                ["packer", "version"], text=True
            ).splitlines()[0],
            "qemu": subprocess.check_output(
                ["qemu-system-aarch64", "--version"], text=True
            ).splitlines()[0],
            "vagrant": subprocess.check_output(
                ["vagrant", "--version"], text=True
            ).strip(),
            "vagrant_qemu": "0.6.3",
        },
    }
    Path("dist/build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()

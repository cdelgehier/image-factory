"""Check the final box and record its source, checksum and tools."""

import hashlib
import json
import shutil
import subprocess
import tarfile as tar
import tempfile
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from image_factory.catalogue import validate_asset
from image_factory.sources import Source
from image_factory.targets import Target, load_target


class BoxDisk(BaseModel):
    """Describe the single packaged QCOW2 disk."""

    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(pattern=r"^box_0\.img$")
    format: str = Field(pattern=r"^qcow2$")


class BoxMetadata(BaseModel):
    """Validate the disk-list format produced by Packer."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str
    architecture: str
    disks: list[BoxDisk] = Field(min_length=1, max_length=1)


def read_box_metadata(path: Path, target: Target) -> dict:
    """Check metadata against the selected recipe contract."""
    metadata = BoxMetadata.model_validate_json(path.read_text())
    if (
        metadata.provider != target.box_provider
        or metadata.architecture != target.architecture
    ):
        raise ValueError("Invalid provider or architecture in box metadata")
    return metadata.model_dump()


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


def unpack_box(box: Path, destination: Path, target: Target | None = None) -> Path:
    """Read only the three expected regular files from Packer."""
    target = target or load_target()
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
            with stream, (destination / member.name).open("wb") as output:
                shutil.copyfileobj(stream, output)
    read_box_metadata(destination / "metadata.json", target)
    vagrantfile = (destination / "Vagrantfile").read_text()
    account_setting = f'config.{target.communicator}.username = "{target.username}"'
    connection_setting = (
        "config.ssh.insert_key = true"
        if target.communicator == "ssh"
        else 'config.vm.communicator = "winrm"'
    )
    if (
        account_setting not in vagrantfile
        or connection_setting not in vagrantfile
        or "PRIVATE KEY" in vagrantfile
        or "image-factory-build" in vagrantfile
    ):
        raise ValueError(
            f"Invalid {target.communicator.upper()} configuration in the box"
        )
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
    target = load_target()
    box = target.box
    validate_asset(box.stat().st_size)
    source = Source.model_validate_json(
        (target.directory / "source.pkrvars.json").read_text()
    )
    disk = target.disk
    info = check_disk(disk)
    with tempfile.TemporaryDirectory(prefix="image-factory-archive-") as directory:
        packaged = unpack_box(box, Path(directory), target)
        metadata = read_box_metadata(Path(directory) / "metadata.json", target)
        check_disk(packaged)
        if sha256(packaged) != sha256(disk):
            raise ValueError("The box does not contain the final standalone disk")
    checksum = sha256(box)
    box.with_suffix(".box.sha256").write_text(f"{checksum}  {box.name}\n")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    manifest = {
        "source": source.model_dump(),
        "target": target.name,
        "communicator": target.communicator,
        f"{target.communicator}_username": target.username,
        "architecture": target.architecture,
        "box_metadata": metadata,
        "accelerator": "tcg",
        "cpu": "max",
        "virtual_size": info["virtual-size"],
        "commit": revision.stdout.strip() or "uncommitted",
        "box": {"filename": box.name, "sha256": checksum, "bytes": box.stat().st_size},
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
    target.manifest.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()

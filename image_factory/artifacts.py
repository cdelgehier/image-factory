"""Check build outputs before they can become release assets.

The packaged disk must match the standalone disk and the selected recipe.
The manifest records this link so publication can check the same files again.
"""

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
from image_factory.sources import BuildSource, Source
from image_factory.targets import Target, load_target


class BoxDisk(BaseModel):
    """Describe the only disk accepted in a box archive.

    A fixed filename and format keep the metadata in line with the files
    that the QEMU provider will import.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(pattern=r"^box_0\.img$")
    format: str = Field(pattern=r"^qcow2$")


class BoxMetadata(BaseModel):
    """Validate Packer's libvirt metadata used by the QEMU provider.

    These recipes produce one disk. Extra fields or disks are rejected so a
    change in the archive format cannot pass without a review.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str
    architecture: str
    disks: list[BoxDisk] = Field(min_length=1, max_length=1)


def read_box_metadata(path: Path, target: Target) -> dict:
    """Check that the archive describes the box requested by the recipe.

    Valid JSON alone is not enough: a box for another provider or CPU
    architecture must not receive this target's manifest or catalogue entry.
    """

    metadata = BoxMetadata.model_validate_json(path.read_text())
    if (
        metadata.provider != target.box_provider
        or metadata.architecture != target.architecture
    ):
        raise ValueError("Invalid provider or architecture in box metadata")
    return metadata.model_dump()


class PackerTarInfo(tar.TarInfo):
    """Read the GNU tar headers written by Packer's Go archive writer.

    GNU access times use the area where USTAR stores a path prefix. Reading
    them as a prefix would change the names of otherwise valid box files.
    """

    @classmethod
    def fromtarfile(cls, tarfile: tar.TarFile) -> Self:
        """Keep Python's header parsing and correct GNU regular file names."""

        archive = tarfile
        offset = archive.fileobj.tell()
        header = archive.fileobj.read(512)
        # Restore the position so the standard parser still reads the full header.
        archive.fileobj.seek(offset)
        member = super().fromtarfile(archive)
        if header[257:265] == tar.GNU_MAGIC and header[156:157] in (
            tar.REGTYPE,
            tar.AREGTYPE,
        ):
            # For these GNU files, the name field is enough; timestamps are not paths.
            member.name = (
                header[:100].split(b"\0", 1)[0].decode(archive.encoding, archive.errors)
            )
        return member


def sha256(path: Path) -> str:
    """Hash a file without loading the disk image into memory."""

    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack_box(box: Path, destination: Path, target: Target | None = None) -> Path:
    """Unpack a box only when its files and connection settings match a recipe.

    The archive is checked before copying any member. Fixed names and regular
    files reject archive paths and links that could write outside the destination.
    """

    target = target or load_target()
    destination.mkdir(parents=True, exist_ok=True)
    with tar.open(box, tarinfo=PackerTarInfo) as archive:
        members = archive.getmembers()
        expected = {"box_0.img", "metadata.json", "Vagrantfile"}
        # The count also rejects duplicate names hidden by the set comparison.
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
    # Clones need the recipe's account, never the temporary Packer credentials.
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
    """Require a healthy QCOW2 disk that can be used on its own.

    A backing file could exist on the build runner but be missing for users.
    The disk must therefore contain all its data before it is packaged.
    """

    info = json.loads(
        subprocess.check_output(
            ["qemu-img", "info", "--output=json", str(path)], text=True
        )
    )
    if info.get("format") != "qcow2" or info.get("backing-filename"):
        raise ValueError("The disk must be a standalone QCOW2 image")
    # Metadata can look correct while the QCOW2 structures are damaged.
    subprocess.run(["qemu-img", "check", str(path)], check=True)
    return info


def check_box_size(target: Target) -> None:
    """Keep GitHub's asset limit separate from private Windows builds."""

    size = target.box.stat().st_size
    if target.publication == "release":
        validate_asset(size)
    elif size == 0:
        raise ValueError("The box must not be empty")


def read_build_source(target: Target) -> BuildSource:
    """Use the actual private ISO without allowing overrides in public releases."""

    record = target.dist / "source.json"
    if target.publication == "local" and record.exists():
        return BuildSource.model_validate_json(record.read_text())
    source = Source.model_validate_json(
        (target.directory / "source.pkrvars.json").read_text()
    )
    return BuildSource(source=source, checksum_verified=True)


def main() -> None:
    """Validate the disk and box, then record their source and build details.

    The checksum and manifest are written only after both outputs pass checks.
    Publication uses these records to detect files changed after the build.
    """

    target = load_target()
    box = target.box
    check_box_size(target)
    source = read_build_source(target)
    disk = target.disk
    info = check_disk(disk)
    with tempfile.TemporaryDirectory(prefix="image-factory-archive-") as directory:
        packaged = unpack_box(box, Path(directory), target)
        metadata = read_box_metadata(Path(directory) / "metadata.json", target)
        check_disk(packaged)
        # A valid archive could still contain an older disk from another build.
        if sha256(packaged) != sha256(disk):
            raise ValueError("The box does not contain the final standalone disk")
    checksum = sha256(box)
    box.with_suffix(".box.sha256").write_text(f"{checksum}  {box.name}\n")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    # Record both hashes: the archive and its disk are checked at different stages.
    manifest = {
        "source": source.source.model_dump(),
        "source_checksum_verified": source.checksum_verified,
        "target": target.name,
        "communicator": target.communicator,
        f"{target.communicator}_username": target.username,
        "architecture": target.architecture,
        "box_metadata": metadata,
        "accelerator": target.accelerator,
        "cpu": target.cpu,
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

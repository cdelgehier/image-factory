import io
import json
import tarfile
from pathlib import Path

import pytest

from image_factory.artifacts import unpack_box


def write_box(path: Path, members: dict[str, bytes]) -> None:
    """Create a small archive for format and safety checks."""
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def test_reads_the_packer_libvirt_archive(tmp_path: Path) -> None:
    box = tmp_path / "test.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": json.dumps(
                {
                    "provider": "libvirt",
                    "architecture": "arm64",
                    "disks": [{"path": "box_0.img", "format": "qcow2"}],
                }
            ).encode(),
            "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
        },
    )
    disk = unpack_box(box, tmp_path / "unpacked")
    assert disk.read_bytes() == b"disk"


def test_reads_gnu_headers_with_access_times(tmp_path: Path) -> None:
    box = tmp_path / "test.box"
    members = {
        "box_0.img": b"disk",
        "metadata.json": json.dumps(
            {
                "provider": "libvirt",
                "architecture": "arm64",
                "disks": [{"path": "box_0.img", "format": "qcow2"}],
            }
        ).encode(),
        "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
    }
    with box.open("wb") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            header = bytearray(member.tobuf(format=tarfile.GNU_FORMAT))
            header[345:357] = b"15263171306\0"
            header[148:156] = b"        "
            header[148:156] = f"{sum(header):06o}\0 ".encode()
            archive.write(header)
            archive.write(content + b"\0" * (-len(content) % 512))
        archive.write(b"\0" * 1024)
    assert unpack_box(box, tmp_path / "unpacked").read_bytes() == b"disk"


@pytest.mark.parametrize("member", ["../escape", "id_ed25519", "/absolute"])
def test_rejects_unexpected_archive_files(tmp_path: Path, member: str) -> None:
    box = tmp_path / "test.box"
    write_box(box, {member: b"secret"})
    with pytest.raises(ValueError):
        unpack_box(box, tmp_path / "unpacked")


@pytest.mark.parametrize(
    "provider,architecture", [("qemu", "arm64"), ("libvirt", "amd64")]
)
def test_rejects_wrong_box_metadata(
    tmp_path: Path, provider: str, architecture: str
) -> None:
    box = tmp_path / "test.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": json.dumps(
                {
                    "provider": provider,
                    "architecture": architecture,
                    "disks": [{"path": "box_0.img", "format": "qcow2"}],
                }
            ).encode(),
            "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
        },
    )
    with pytest.raises(ValueError):
        unpack_box(box, tmp_path / "unpacked")

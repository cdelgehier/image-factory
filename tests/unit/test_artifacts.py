import io
import json
import tarfile
from pathlib import Path

import pytest

from image_factory.artifacts import unpack_box
from image_factory.targets import Target, load_target


def test_windows_box_can_use_its_own_winrm_account(tmp_path: Path) -> None:
    values = load_target().model_dump() | {
        "name": "windows-11-arm64",
        "communicator": "winrm",
        "ssh_username": None,
        "winrm_username": "Administrator",
        "runtime_suite": "windows",
    }
    target = Target.model_validate(values)
    box = tmp_path / "windows.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": b'{"provider":"libvirt","architecture":"arm64","disks":[{"path":"box_0.img","format":"qcow2"}]}',
            "Vagrantfile": b'config.vm.communicator = "winrm"\nconfig.winrm.username = "Administrator"\n',
        },
    )
    assert unpack_box(box, tmp_path / "unpacked", target).read_bytes() == b"disk"


@pytest.mark.parametrize("username", ["ubuntu", "debian"])
def test_box_ssh_account_must_match_the_selected_target(
    tmp_path: Path, username: str
) -> None:
    target = load_target().model_copy(
        update={"name": "ubuntu-26.04-arm64", "ssh_username": "ubuntu"}
    )
    box = tmp_path / "test.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": b'{"provider":"libvirt","architecture":"arm64","disks":[{"path":"box_0.img","format":"qcow2"}]}',
            "Vagrantfile": f'config.ssh.username = "{username}"\nconfig.ssh.insert_key = true\n'.encode(),
        },
    )
    if username == "ubuntu":
        assert unpack_box(box, tmp_path / "unpacked", target).read_bytes() == b"disk"
    else:
        with pytest.raises(ValueError, match="SSH configuration"):
            unpack_box(box, tmp_path / "unpacked", target)


@pytest.mark.parametrize(
    "configuration",
    [
        'config.ssh.username = "debian"\nconfig.ssh.insert_key = false',
        "config.ssh.insert_key = true",
        'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\nPRIVATE KEY',
        'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\nimage-factory-build',
    ],
)
def test_box_rejects_missing_key_replacement_or_build_credentials(
    tmp_path: Path, configuration: str
) -> None:
    box = tmp_path / "test.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": b'{"provider":"libvirt","architecture":"arm64","disks":[{"path":"box_0.img","format":"qcow2"}]}',
            "Vagrantfile": configuration.encode(),
        },
    )
    with pytest.raises(ValueError, match="SSH configuration"):
        unpack_box(box, tmp_path / "unpacked")


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        "libvirt",
        {},
        {"provider": "libvirt", "architecture": "arm64"},
        {"provider": "libvirt", "architecture": "arm64", "disks": []},
        {
            "provider": "libvirt",
            "architecture": "arm64",
            "disks": [{"path": "box_0.img", "format": "raw"}],
        },
        {
            "provider": "libvirt",
            "architecture": "arm64",
            "disks": [{"path": "../disk.img", "format": "qcow2"}],
        },
        {
            "provider": "libvirt",
            "architecture": "arm64",
            "disks": [{"path": "box_0.img", "format": "qcow2"}] * 2,
        },
        {
            "provider": "libvirt",
            "architecture": "arm64",
            "disks": [
                {"path": "box_0.img", "format": "qcow2", "backing_file": "base.img"}
            ],
        },
        {"provider": "libvirt", "architecture": "arm64", "disks": "box_0.img"},
    ],
)
def test_rejects_incomplete_or_invalid_disk_metadata(
    tmp_path: Path, metadata: object
) -> None:
    box = tmp_path / "test.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": json.dumps(metadata).encode(),
            "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
        },
    )
    with pytest.raises(ValueError):
        unpack_box(box, tmp_path / "unpacked")


def test_rejects_malformed_metadata_json(tmp_path: Path) -> None:
    box = tmp_path / "test.box"
    write_box(
        box,
        {
            "box_0.img": b"disk",
            "metadata.json": b'{"provider":',
            "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
        },
    )
    with pytest.raises(ValueError):
        unpack_box(box, tmp_path / "unpacked")


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

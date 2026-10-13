import io
import json
import tarfile
from pathlib import Path

import pytest

from image_factory.artifacts import unpack_box
from image_factory.targets import Target, load_target


@pytest.mark.parametrize("verified", [False, True])
def test_private_manifest_reads_the_source_used_by_the_build(
    tmp_path: Path, verified: bool
) -> None:
    from image_factory.artifacts import read_build_source

    target = load_target("windows-11-arm64").model_copy(update={"root": tmp_path})
    target.dist.mkdir(parents=True)
    source = {
        "source_url": "https://www.microsoft.com/en-us/software-download/windows11arm64",
        "source_build": "26300.9457",
        "source_checksum": "sha256:" + "ab" * 32,
    }
    (target.dist / "source.json").write_text(
        json.dumps({"source": source, "checksum_verified": verified})
    )
    record = read_build_source(target)
    assert record.source.model_dump() == source
    assert record.checksum_verified is verified


@pytest.mark.parametrize("checksum", ["none", "invalid", "sha256:abc"])
def test_private_build_source_rejects_invalid_recorded_checksums(
    tmp_path: Path, checksum: str
) -> None:
    from image_factory.artifacts import read_build_source

    target = load_target("windows-11-arm64").model_copy(update={"root": tmp_path})
    target.dist.mkdir(parents=True)
    (target.dist / "source.json").write_text(
        json.dumps(
            {
                "source": {
                    "source_url": "https://www.microsoft.com/en-us/software-download/windows11arm64",
                    "source_build": "26300.9457",
                    "source_checksum": checksum,
                },
                "checksum_verified": False,
            }
        )
    )
    with pytest.raises(ValueError):
        read_build_source(target)


def test_public_manifest_uses_the_reviewed_source_pin(tmp_path: Path) -> None:
    from image_factory.artifacts import read_build_source

    target = load_target("debian-13-arm64").model_copy(update={"root": tmp_path})
    target.directory.mkdir(parents=True)
    target.dist.mkdir(parents=True)
    source = {
        "source_url": "https://cloud.debian.org/images/cloud/trixie/20261012-0000/debian-13-generic-arm64.qcow2",
        "source_build": "20261012-0000",
        "source_checksum": "sha512:" + "ab" * 64,
    }
    (target.directory / "source.pkrvars.json").write_text(json.dumps(source))
    (target.dist / "source.json").write_text("invalid private record")
    record = read_build_source(target)
    assert record.source.model_dump() == source
    assert record.checksum_verified is True


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
    """Create small real tar archives without downloading a guest disk.

    Archive checks use names, types, metadata and connection settings. The
    disk bytes are a placeholder; the box test suite checks QEMU integrity.
    """

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
            # Reproduce Go's GNU access time in the area used by a USTAR prefix.
            header[345:357] = b"15263171306\0"
            # Changing header bytes requires a new tar checksum before reading it.
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


@pytest.mark.parametrize("publication", ["local", "release"])
def test_private_boxes_can_exceed_the_github_asset_limit(
    tmp_path: Path, publication: str
) -> None:
    from image_factory.artifacts import check_box_size

    target = load_target().model_copy(
        update={"root": tmp_path, "publication": publication}
    )
    target.dist.mkdir(parents=True)
    # A sparse file exercises the real size boundary without allocating 2 GiB.
    with target.box.open("wb") as box:
        box.truncate(2**31)
    if publication == "local":
        check_box_size(target)
    else:
        with pytest.raises(ValueError, match="2 GiB"):
            check_box_size(target)


def test_private_boxes_must_not_be_empty(tmp_path: Path) -> None:
    from image_factory.artifacts import check_box_size

    target = load_target().model_copy(update={"root": tmp_path, "publication": "local"})
    target.dist.mkdir(parents=True)
    target.box.touch()
    with pytest.raises(ValueError, match="empty"):
        check_box_size(target)

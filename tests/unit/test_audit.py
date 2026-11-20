import io
import json
import subprocess
import tarfile
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_factory import audit
from image_factory.artifacts import sha256
from image_factory.catalogue import add_version
from image_factory.signatures import signature_files
from image_factory.targets import Target, load_target


def copy_release_asset(
    storage: dict[str, bytes], url: str, destination: Path, *, limit: int
) -> None:
    """Replace the remote download while keeping real archive and disk checks."""

    name = url.rsplit("/", 1)[1]
    if name not in storage:
        raise FileNotFoundError(name)
    destination.write_bytes(storage[name])


@pytest.fixture
def published_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Target, dict, dict[str, bytes]]:
    """Publish a tiny real QCOW2 archive through an in-memory HTTP boundary."""

    target = load_target().model_copy(update={"root": tmp_path / "source"})
    target.disk.parent.mkdir(parents=True)
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", str(target.disk), "1M"],
        check=True,
        capture_output=True,
    )
    metadata = {
        "provider": "libvirt",
        "architecture": "arm64",
        "disks": [{"path": "box_0.img", "format": "qcow2"}],
    }
    with tarfile.open(target.box, "w:gz") as archive:
        for name, content in {
            "box_0.img": target.disk.read_bytes(),
            "metadata.json": json.dumps(metadata).encode(),
            "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
        }.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    checksum = sha256(target.box)
    manifest = {
        "schema_version": 1,
        "built_at": "2026-11-20T12:00:00Z",
        "build_url": "https://github.com/cdelgehier/image-factory/actions/runs/123/attempts/1",
        "commit": "ab" * 20,
        "target": target.name,
        "architecture": "arm64",
        "ssh_username": "debian",
        "source_checksum_verified": True,
        "box_metadata": metadata,
        "box": {
            "filename": target.box.name,
            "sha256": checksum,
            "bytes": target.box.stat().st_size,
        },
        "disk_sha256": sha256(target.disk),
        "virtual_size": 1024**2,
        "signatures": {
            "box": target.box.name + ".bundle",
            "manifest": target.manifest.name + ".bundle",
        },
    }
    storage = {
        target.box.name: target.box.read_bytes(),
        target.box.name + ".sha256": f"{checksum}  {target.box.name}\n".encode(),
        target.manifest.name: json.dumps(manifest).encode(),
        target.report.name: b'<testsuites><testsuite tests="7" failures="0" errors="0" skipped="0"/></testsuites>',
    }
    storage.update(
        {path.name: b"certificate bundle" for path in signature_files(target)}
    )
    monkeypatch.setattr(audit, "download_file", partial(copy_release_asset, storage))
    monkeypatch.setattr(audit, "verify_target", Mock())
    monkeypatch.setattr(audit, "release_requires_signatures", Mock(return_value=True))
    return target, add_version(None, "v0.7.0", checksum, target), storage


def test_audit_checks_the_downloaded_box_and_its_signed_records(
    published_box: tuple[Target, dict, dict[str, bytes]], tmp_path: Path
) -> None:
    target, catalogue, _ = published_box
    result = audit.audit_target(target, "v0.7.0", catalogue, tmp_path / "audit")
    assert result["status"] == "passed"
    assert result["signatures"] == "verified"
    assert result["target"] == target.name


@pytest.mark.parametrize("artifact", ["box", "checksum", "disk", "report"])
def test_audit_rejects_damaged_published_assets(
    published_box: tuple[Target, dict, dict[str, bytes]], tmp_path: Path, artifact: str
) -> None:
    target, catalogue, storage = published_box
    if artifact == "box":
        storage[target.box.name] = b"damaged box"
    elif artifact == "checksum":
        storage[target.box.name + ".sha256"] = b"incorrect checksum"
    elif artifact == "disk":
        manifest = json.loads(storage[target.manifest.name])
        manifest["disk_sha256"] = "00" * 32
        storage[target.manifest.name] = json.dumps(manifest).encode()
    else:
        storage[target.report.name] = storage[target.report.name].replace(
            b'failures="0"', b'failures="1"'
        )
    with pytest.raises(ValueError):
        audit.audit_target(target, "v0.7.0", catalogue, tmp_path / "audit")


def test_new_releases_cannot_fall_back_to_unsigned_verification(
    published_box: tuple[Target, dict, dict[str, bytes]], tmp_path: Path
) -> None:
    target, catalogue, storage = published_box
    del storage[target.box.name + ".bundle"]
    with pytest.raises(FileNotFoundError):
        audit.audit_target(target, "v0.7.0", catalogue, tmp_path / "audit")


def test_invalid_signatures_stop_the_audit_before_unpacking(
    published_box: tuple[Target, dict, dict[str, bytes]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, catalogue, _ = published_box
    monkeypatch.setattr(
        audit,
        "verify_target",
        Mock(side_effect=subprocess.CalledProcessError(1, "cosign")),
    )
    unpack = Mock()
    monkeypatch.setattr(audit, "unpack_box", unpack)
    with pytest.raises(subprocess.CalledProcessError):
        audit.audit_target(target, "v0.7.0", catalogue, tmp_path / "audit")
    assert not unpack.called


def test_legacy_release_is_reported_as_unsigned_not_signature_verified(
    published_box: tuple[Target, dict, dict[str, bytes]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, _, storage = published_box
    manifest = json.loads(storage[target.manifest.name])
    del manifest["signatures"]
    storage[target.manifest.name] = json.dumps(manifest).encode()
    catalogue = add_version(None, "v0.4.0", sha256(target.box), target)
    monkeypatch.setattr(audit, "release_requires_signatures", Mock(return_value=False))
    verify = Mock()
    monkeypatch.setattr(audit, "verify_target", verify)
    result = audit.audit_target(target, "v0.4.0", catalogue, tmp_path / "audit")
    assert result["signatures"] == "legacy_unsigned"
    assert not verify.called


def test_audit_rejects_catalogue_urls_outside_the_expected_release(
    published_box: tuple[Target, dict, dict[str, bytes]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, catalogue, _ = published_box
    catalogue["versions"][0]["providers"][0]["url"] = "https://example.com/other.box"
    download = Mock()
    monkeypatch.setattr(audit, "download_file", download)
    with pytest.raises(ValueError):
        audit.audit_target(target, "v0.7.0", catalogue, tmp_path / "audit")
    assert not download.called


def test_downloads_are_bounded_even_without_a_content_length(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "urlopen", Mock(return_value=io.BytesIO(b"12345")))
    with pytest.raises(ValueError, match="size limit"):
        audit.download_file("https://github.com/asset", tmp_path / "asset", limit=4)


@pytest.mark.parametrize("signing_code", [False, True])
def test_signing_policy_uses_the_tagged_source_not_a_version_guess(
    monkeypatch: pytest.MonkeyPatch,
    signing_code: bool,
) -> None:
    paths = [{"path": "README.md", "type": "blob"}]
    if signing_code:
        paths.append({"path": "image_factory/signatures.py", "type": "blob"})
    monkeypatch.setattr(
        audit, "gh", Mock(return_value=json.dumps({"tree": paths, "truncated": False}))
    )
    assert audit.release_requires_signatures("v1.0.0") is signing_code


def test_incomplete_tag_metadata_cannot_downgrade_signature_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit, "gh", Mock(return_value='{"tree": [], "truncated": true}')
    )
    with pytest.raises(ValueError, match="source tree"):
        audit.release_requires_signatures("v0.7.0")


def test_archive_extraction_is_bounded_before_large_members_are_read(
    tmp_path: Path,
) -> None:
    box = tmp_path / "large.box"
    member = tarfile.TarInfo("box_0.img")
    member.size = audit.MAX_EXTRACTED_BYTES + 1
    box.write_bytes(member.tobuf())
    with pytest.raises(ValueError, match="extraction limits"):
        audit.check_archive_size(box)

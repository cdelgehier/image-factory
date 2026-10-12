import io
import json
import sys
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_factory import release
from image_factory.artifacts import sha256
from image_factory.release import require_test_report
from image_factory.targets import Target, load_target


def test_release_dispatch_uses_the_workflow_from_the_requested_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = Mock(return_value="dispatched")
    monkeypatch.setattr(release, "gh", github)
    monkeypatch.setattr(sys, "argv", ["release", "dispatch", "v0.1.0"])
    release.main()
    arguments = github.call_args.args
    assert arguments[:3] == ("workflow", "run", "release.yml")
    assert arguments[arguments.index("--ref") + 1] == "v0.1.0"


@pytest.fixture
def tested_target(tmp_path: Path) -> Target:
    """Create a small box with its matching release records."""
    target = load_target().model_copy(update={"root": tmp_path})
    target.dist.mkdir(parents=True)
    metadata = {
        "provider": "libvirt",
        "architecture": "arm64",
        "disks": [{"path": "box_0.img", "format": "qcow2"}],
    }
    members = {
        "box_0.img": b"disk",
        "metadata.json": json.dumps(metadata).encode(),
        "Vagrantfile": b'config.ssh.username = "debian"\nconfig.ssh.insert_key = true\n',
    }
    with tarfile.open(target.box, "w:gz") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    disk = tmp_path / "disk"
    disk.write_bytes(b"disk")
    checksum = sha256(target.box)
    target.box.with_suffix(".box.sha256").write_text(f"{checksum}  {target.box.name}\n")
    target.manifest.write_text(
        json.dumps(
            {
                "commit": "ab" * 20,
                "target": target.name,
                "architecture": "arm64",
                "ssh_username": "debian",
                "box_metadata": metadata,
                "box": {
                    "filename": target.box.name,
                    "sha256": checksum,
                    "bytes": target.box.stat().st_size,
                },
                "disk_sha256": sha256(disk),
            }
        )
    )
    target.report.write_text(
        '<testsuites><testsuite tests="7" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    return target


def test_release_assets_have_unique_names_and_a_matching_catalogue(
    tested_target: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release, "previous_catalogue", Mock(return_value=None))
    assets = release.checked_assets("v0.2.0", "ab" * 20, tested_target)
    assert {asset.name for asset in assets} == {
        "debian-13-arm64.box",
        "debian-13-arm64.box.sha256",
        "debian-13-arm64-build-manifest.json",
        "debian-13-arm64-test-report.xml",
        "debian-13-arm64.json",
    }
    catalogue = json.loads(
        (tested_target.dist / tested_target.catalogue_file).read_text()
    )
    assert catalogue["name"] == "cdelgehier/debian-13-arm64"
    assert all(
        provider["checksum"] == sha256(tested_target.box)
        and provider["architecture"] == tested_target.architecture
        for provider in catalogue["versions"][0]["providers"]
    )


@pytest.mark.parametrize(
    "field,value", [("commit", "cd" * 20), ("disk_sha256", "00" * 32)]
)
def test_release_rejects_a_manifest_from_another_build(
    tested_target: Target, field: str, value: str
) -> None:
    manifest = json.loads(tested_target.manifest.read_text())
    manifest[field] = value
    tested_target.manifest.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest"):
        release.checked_assets("v0.2.0", "ab" * 20, tested_target)


def test_release_rejects_a_changed_checksum_file(tested_target: Target) -> None:
    tested_target.box.with_suffix(".box.sha256").write_text(
        "00" * 32 + f"  {tested_target.box.name}\n"
    )
    with pytest.raises(ValueError, match="checksum"):
        release.checked_assets("v0.2.0", "ab" * 20, tested_target)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "ubuntu-26.04-arm64"),
        ("architecture", "amd64"),
        ("ssh_username", "vagrant"),
        ("box_metadata", {"provider": "qemu", "architecture": "arm64", "disks": []}),
        ("box", {"filename": "wrong.box", "sha256": "ab" * 32, "bytes": 123}),
        ("box", None),
        ("box", []),
    ],
)
def test_release_rejects_manifest_metadata_from_another_target(
    field: str, value: object
) -> None:
    target = load_target()
    manifest = {
        "target": target.name,
        "architecture": "arm64",
        "ssh_username": "debian",
        "box_metadata": {
            "provider": "libvirt",
            "architecture": "arm64",
            "disks": [{"path": "box_0.img", "format": "qcow2"}],
        },
        "box": {"filename": target.box.name, "sha256": "ab" * 32, "bytes": 123},
    }
    manifest[field] = value
    with pytest.raises(ValueError):
        release.validate_manifest(manifest, target)


def test_accepts_a_successful_vm_report(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite tests="7" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    require_test_report(report)


@pytest.mark.parametrize(
    "tests,failures,errors,skipped",
    [
        ("0", "0", "0", "0"),
        ("7", "1", "0", "0"),
        ("7", "0", "1", "0"),
        ("7", "0", "0", "1"),
    ],
)
def test_does_not_publish_without_all_vm_tests(
    tmp_path: Path, tests: str, failures: str, errors: str, skipped: str
) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}"/></testsuites>'
    )
    with pytest.raises(ValueError):
        require_test_report(report)


def test_draft_does_not_change_a_published_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"v0.1.0": {"draft": False}}
    calls = []
    monkeypatch.setattr(release, "release_record", records.get)
    monkeypatch.setattr(release, "gh", calls.append)
    with pytest.raises(ValueError, match="already published"):
        release.draft("v0.1.0")
    assert calls == []


def test_draft_can_be_retried_without_changing_the_existing_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"v0.1.0": {"draft": True}}
    calls = []
    monkeypatch.setattr(release, "release_record", records.get)
    monkeypatch.setattr(release, "gh", calls.append)
    release.draft("v0.1.0")
    assert calls == []

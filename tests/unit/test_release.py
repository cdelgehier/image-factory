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


def test_release_rejects_a_local_recipe_before_reading_its_files() -> None:
    target = load_target().model_copy(update={"publication": "local"})
    with pytest.raises(ValueError, match="local"):
        release.checked_assets("v0.2.0", "ab" * 20, target)


@pytest.fixture
def tested_target(tmp_path: Path) -> Target:
    """Create matching release records without building or booting a guest.

    The fixture uses a real archive and real hashes so each test can change
    one record and prove that publication detects the mismatch.
    """

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


@pytest.fixture
def draft_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Keep real asset bodies while isolating Git and the GitHub boundary."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    monkeypatch.setattr(
        release.subprocess, "check_output", Mock(return_value="ab" * 20)
    )
    target = load_target()
    monkeypatch.setattr(release, "enabled_targets", Mock(return_value=[target]))
    assets = []
    for name, body in {
        "debian-13-arm64.box": b"tested box",
        "debian-13-arm64-build-manifest.json": b"{}",
        "debian-13-arm64.json": b'{"name":"cdelgehier/debian-13-arm64","versions":[]}',
    }.items():
        asset = tmp_path / name
        asset.write_bytes(body)
        assets.append(asset)
    monkeypatch.setattr(release, "checked_assets", Mock(return_value=assets))
    monkeypatch.setattr(
        release,
        "release_record",
        Mock(return_value={"id": 42, "draft": True, "assets": []}),
    )
    return assets


def test_published_json_assets_use_the_content_type_vagrant_requires(
    draft_assets: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [""]
    for asset in draft_assets[1:]:
        responses.append(
            json.dumps(
                {
                    "content_type": "application/json",
                    "state": "uploaded",
                    "size": asset.stat().st_size,
                }
            )
        )
    responses.append("")
    github = Mock(side_effect=responses)
    monkeypatch.setattr(release, "gh", github)

    release.publish("v0.2.0")

    uploads = [call.args for call in github.call_args_list if call.args[0] == "api"]
    assert len(uploads) == 2
    for arguments, asset in zip(uploads, draft_assets[1:], strict=True):
        assert (
            "--method" in arguments
            and arguments[arguments.index("--method") + 1] == "POST"
        )
        assert (
            arguments[arguments.index("--header") + 1]
            == "Content-Type: application/json"
        )
        assert arguments[arguments.index("--input") + 1] == str(asset)
        assert f"/releases/42/assets?name={asset.name}" in arguments[1]
    binary_upload = github.call_args_list[0].args
    assert str(draft_assets[0]) in binary_upload
    assert all(str(asset) not in binary_upload for asset in draft_assets[1:])
    assert github.call_args_list[-1].args == (
        "release",
        "edit",
        "v0.2.0",
        "--repo",
        "cdelgehier/image-factory",
        "--draft=false",
        "--latest",
    )


@pytest.mark.parametrize(
    "response",
    [
        {"content_type": "application/octet-stream", "state": "uploaded", "size": 2},
        {"content_type": "application/json", "state": "starter", "size": 2},
        {"content_type": "application/json", "state": "uploaded", "size": 1},
    ],
)
def test_bad_json_upload_keeps_the_release_unpublished(
    draft_assets: list[Path], response: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    github = Mock(return_value=json.dumps(response))
    monkeypatch.setattr(release, "gh", github)
    with pytest.raises(ValueError, match="JSON upload"):
        release.publish("v0.2.0")
    assert not any(
        call.args[:2] == ("release", "edit") for call in github.call_args_list
    )


def test_retry_replaces_only_json_assets_in_the_unpublished_draft(
    draft_assets: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release,
        "release_record",
        Mock(
            return_value={
                "id": 42,
                "draft": True,
                "assets": [{"id": 7, "name": draft_assets[1].name}],
            }
        ),
    )
    github = Mock(
        side_effect=[
            "",
            "",
            json.dumps(
                {
                    "content_type": "application/json",
                    "state": "uploaded",
                    "size": draft_assets[1].stat().st_size,
                }
            ),
            json.dumps(
                {
                    "content_type": "application/json",
                    "state": "uploaded",
                    "size": draft_assets[2].stat().st_size,
                }
            ),
            "",
        ]
    )
    monkeypatch.setattr(release, "gh", github)
    release.publish("v0.2.0")
    assert github.call_args_list[1].args == (
        "api",
        "repos/cdelgehier/image-factory/releases/assets/7",
        "--method",
        "DELETE",
    )


def test_publish_does_not_replace_assets_on_an_existing_public_release(
    draft_assets: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release, "release_record", Mock(return_value={"id": 42, "draft": False})
    )
    github = Mock()
    monkeypatch.setattr(release, "gh", github)
    with pytest.raises(ValueError, match="draft release"):
        release.publish("v0.2.0")
    github.assert_not_called()

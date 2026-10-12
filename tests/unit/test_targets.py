import json
import sys
from functools import partial
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_factory import targets
from image_factory.targets import enabled_targets, load_target


def write_target(root: Path, name: str, username: str) -> None:
    """Create a small recipe configuration for selection tests."""
    directory = root / "targets" / name
    directory.mkdir(parents=True)
    (directory / "target.json").write_text(
        json.dumps(
            {
                "display_name": name,
                "ssh_username": username,
                "architecture": "arm64",
                "box_provider": "libvirt",
                "runtime_suite": "linux",
                "minimum_tests": 7,
            }
        )
    )


def test_default_and_selected_targets_have_separate_outputs(tmp_path: Path) -> None:
    write_target(tmp_path, "debian-13-arm64", "debian")
    write_target(tmp_path, "ubuntu-26.04-arm64", "ubuntu")
    (tmp_path / "targets.json").write_text(
        json.dumps(
            {
                "default": "debian-13-arm64",
                "enabled": ["debian-13-arm64", "ubuntu-26.04-arm64"],
            }
        )
    )
    assert load_target(root=tmp_path).ssh_username == "debian"
    ubuntu = load_target("ubuntu-26.04-arm64", root=tmp_path)
    assert ubuntu.ssh_username == "ubuntu"
    assert ubuntu.box == tmp_path / "dist/ubuntu-26.04-arm64/ubuntu-26.04-arm64.box"
    assert ubuntu.catalogue_file == "ubuntu-26.04-arm64.json"
    assert [target.name for target in enabled_targets(tmp_path)] == [
        "debian-13-arm64",
        "ubuntu-26.04-arm64",
    ]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "missing"])
def test_rejects_unknown_or_unsafe_target_names(tmp_path: Path, name: str) -> None:
    (tmp_path / "targets.json").write_text('{"default":"debian","enabled":["debian"]}')
    with pytest.raises(ValueError):
        load_target(name, root=tmp_path)


@pytest.mark.parametrize(
    "config",
    [
        {"default": "missing", "enabled": ["debian"]},
        {"default": "debian", "enabled": []},
        {"default": "debian", "enabled": ["debian", "debian"]},
    ],
)
def test_rejects_an_invalid_target_list(tmp_path: Path, config: dict) -> None:
    (tmp_path / "targets.json").write_text(json.dumps(config))
    with pytest.raises(ValueError):
        enabled_targets(tmp_path)


def test_workflow_matrix_lists_every_enabled_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    write_target(tmp_path, "debian-13-arm64", "debian")
    write_target(tmp_path, "ubuntu-26.04-arm64", "ubuntu")
    (tmp_path / "targets.json").write_text(
        '{"default":"debian-13-arm64","enabled":["debian-13-arm64","ubuntu-26.04-arm64"]}'
    )
    monkeypatch.setattr(
        targets, "enabled_targets", partial(enabled_targets, root=tmp_path)
    )
    monkeypatch.setattr(sys, "argv", ["targets", "matrix"])
    targets.main()
    assert json.loads(capsys.readouterr().out) == {
        "target": ["debian-13-arm64", "ubuntu-26.04-arm64"]
    }


def test_runtime_command_uses_the_selected_suite_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_target(tmp_path, "ubuntu-26.04-arm64", "ubuntu")
    (tmp_path / "targets.json").write_text(
        '{"default":"ubuntu-26.04-arm64","enabled":["ubuntu-26.04-arm64"]}'
    )
    suite = tmp_path / "tests/box/linux"
    suite.mkdir(parents=True)
    run = Mock()
    monkeypatch.setattr(targets, "ROOT", tmp_path)
    monkeypatch.setattr(targets, "load_target", partial(load_target, root=tmp_path))
    monkeypatch.setattr(targets.subprocess, "run", run)
    monkeypatch.setattr(
        sys, "argv", ["targets", "test", "--target", "ubuntu-26.04-arm64"]
    )
    targets.main()
    command = run.call_args.args[0]
    assert str(suite) in command
    assert (
        f"--junitxml={tmp_path}/dist/ubuntu-26.04-arm64/ubuntu-26.04-arm64-test-report.xml"
        in command
    )
    assert run.call_args.kwargs["env"]["IMAGE_FACTORY_TARGET"] == "ubuntu-26.04-arm64"

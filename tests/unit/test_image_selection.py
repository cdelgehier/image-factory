import subprocess
from pathlib import Path

import pytest

from image_factory import image_selection
from image_factory.targets import all_targets


@pytest.mark.parametrize(
    "paths,expected",
    [
        ([], []),
        (["README.md", "docs/tutorials/add-rocky-linux.md"], []),
        (["targets/debian-13-arm64/README.md"], []),
        (["targets/windows-11-arm64/cleanup.ps1"], []),
        (["tests/box/windows/test_guest.py"], []),
        (["targets/windows-11-arm64/tests/unit/test_windows_prepare.py"], []),
        (["tests/unit/test_targets.py"], []),
        (["targets/debian-13-arm64/source.pkrvars.json"], ["debian-13-arm64"]),
        (["targets/ubuntu-26.04-arm64/build.pkr.hcl"], ["ubuntu-26.04-arm64"]),
        (
            [
                "targets/debian-13-arm64/cleanup.sh",
                "targets/ubuntu-26.04-arm64/prepare.sh",
            ],
            ["debian-13-arm64", "ubuntu-26.04-arm64"],
        ),
        (["image_factory/artifacts.py"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
        (["scripts/build.sh"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
        (["tests/box/linux/test_guest.py"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
        (["tests/box/conftest.py"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
        (["targets.json"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
        (["uv.lock"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
        (
            [".github/actions/setup/action.yml"],
            ["debian-13-arm64", "ubuntu-26.04-arm64"],
        ),
        (
            [".github/workflows/image-tests.yml"],
            ["debian-13-arm64", "ubuntu-26.04-arm64"],
        ),
        (["new-build-tool.py"], ["debian-13-arm64", "ubuntu-26.04-arm64"]),
    ],
)
def test_image_jobs_only_cover_recipes_affected_by_the_change(
    paths: list[str], expected: list[str]
) -> None:
    selected = image_selection.affected_targets(paths, all_targets())
    assert [target.name for target in selected] == expected


def git(root: Path, *arguments: str) -> str:
    """Use a real temporary repository to test Git diff behavior."""

    return subprocess.check_output(["git", *arguments], cwd=root, text=True).strip()


def test_pull_request_diff_handles_deletions_renames_and_unrelated_main_changes(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "--initial-branch=main")
    git(tmp_path, "config", "user.name", "Image tests")
    git(tmp_path, "config", "user.email", "tests@example.com")
    (tmp_path / "old.sh").write_text("initial source\n")
    (tmp_path / "removed.sh").write_text("removed source\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initial files")
    git(tmp_path, "checkout", "-b", "recipe")
    git(tmp_path, "mv", "old.sh", "new.sh")
    git(tmp_path, "rm", "removed.sh")
    git(tmp_path, "commit", "-m", "update recipe")
    git(tmp_path, "checkout", "main")
    (tmp_path / "unrelated.sh").write_text("another branch\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "unrelated main change")
    assert image_selection.changed_paths("main", "recipe", root=tmp_path) == [
        "new.sh",
        "old.sh",
        "removed.sh",
    ]


def test_invalid_diff_reference_fails_instead_of_skipping_image_checks(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init")
    with pytest.raises(subprocess.CalledProcessError):
        image_selection.changed_paths("--all", "HEAD", root=tmp_path)

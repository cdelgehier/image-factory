"""Draft and publish releases after the tagged box passes its tests."""

import argparse
import json
import re
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from image_factory.artifacts import sha256
from image_factory.catalogue import (
    BOX_FILE,
    REPOSITORY,
    TAG_PATTERN,
    add_version,
    validate_asset,
    version_key,
)


def gh(*arguments: str) -> str:
    """Run GitHub CLI without using a shell."""
    return subprocess.check_output(["gh", *arguments], text=True)


def require_test_report(path: Path) -> None:
    """Reject empty reports, failed tests and skipped tests."""
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    if not suites or sum(int(suite.get("tests", "0")) for suite in suites) < 6:
        raise ValueError("The report must include all box tests")
    if any(
        int(suite.get(field, "0"))
        for suite in suites
        for field in ("failures", "errors", "skipped")
    ):
        raise ValueError("All box tests must pass without skips")


def release_record(tag: str) -> dict[str, Any] | None:
    """Find a release while preserving API errors."""
    releases = json.loads(
        gh("api", f"repos/{REPOSITORY}/releases", "--paginate", "--slurp")
    )
    return next(
        (
            release
            for page in releases
            for release in page
            if release["tag_name"] == tag
        ),
        None,
    )


def draft(tag: str) -> None:
    """Create a draft without changing an existing release."""
    record = release_record(tag)
    if record is not None:
        if not record["draft"]:
            raise ValueError("The release is already published")
        return
    gh(
        "release",
        "create",
        tag,
        "--repo",
        REPOSITORY,
        "--verify-tag",
        "--draft",
        "--title",
        tag,
        "--generate-notes",
    )


def release_version_key(release: dict[str, Any]) -> tuple[int, ...]:
    """Compare GitHub releases by stable SemVer."""
    return version_key({"version": release["tag_name"][1:]})


def previous_catalogue(tag: str) -> dict[str, Any] | None:
    """Read the latest catalogue and prevent older releases becoming latest."""
    pages = json.loads(
        gh("api", f"repos/{REPOSITORY}/releases", "--paginate", "--slurp")
    )
    published = [
        release
        for page in pages
        for release in page
        if not release["draft"] and not release["prerelease"]
    ]
    if not published:
        return None
    if any(not re.fullmatch(TAG_PATTERN, release["tag_name"]) for release in published):
        raise ValueError("Unexpected published release tag")
    latest = max(published, key=release_version_key)
    if version_key({"version": tag[1:]}) <= release_version_key(latest):
        raise ValueError("Publish a version newer than the latest release")
    with tempfile.TemporaryDirectory(prefix="image-factory-catalogue-") as directory:
        gh(
            "release",
            "download",
            latest["tag_name"],
            "--repo",
            REPOSITORY,
            "--pattern",
            "debian-13-arm64.json",
            "--dir",
            directory,
        )
        return json.loads((Path(directory) / "debian-13-arm64.json").read_text())


def publish(tag: str) -> None:
    """Upload checked assets to a draft and publish it as the final step."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tagged = subprocess.check_output(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], text=True
    ).strip()
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    manifest = json.loads(Path("dist/build-manifest.json").read_text())
    if head != tagged or manifest["commit"] != head or version != tag[1:]:
        raise ValueError(
            "The version, manifest and checkout must match the release tag"
        )
    record = release_record(tag)
    if record is None or not record["draft"]:
        raise ValueError("Publication requires an existing draft release")
    require_test_report(Path("dist/test-report.xml"))
    checksum = sha256(Path("dist") / BOX_FILE)
    if (
        checksum != manifest["box"]["sha256"]
        or Path(f"dist/{BOX_FILE}.sha256").read_text() != f"{checksum}  {BOX_FILE}\n"
    ):
        raise ValueError("The release checksum does not match the tested box")
    catalogue = add_version(previous_catalogue(tag), tag, checksum)
    Path("dist/debian-13-arm64.json").write_text(json.dumps(catalogue, indent=2) + "\n")
    assets = [
        Path("dist") / name
        for name in (
            BOX_FILE,
            f"{BOX_FILE}.sha256",
            "build-manifest.json",
            "test-report.xml",
            "debian-13-arm64.json",
        )
    ]
    for asset in assets:
        validate_asset(asset.stat().st_size)
    gh("release", "upload", tag, *map(str, assets), "--repo", REPOSITORY, "--clobber")
    gh("release", "edit", tag, "--repo", REPOSITORY, "--draft=false", "--latest")


def main() -> None:
    """Dispatch, draft or publish a stable SemVer tag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("dispatch", "draft", "publish"))
    parser.add_argument("tag")
    arguments = parser.parse_args()
    if not re.fullmatch(TAG_PATTERN, arguments.tag):
        parser.error("Use a stable tag such as v0.1.0")
    if arguments.command == "dispatch":
        gh("api", f"repos/{REPOSITORY}/git/ref/tags/{arguments.tag}")
        print(
            gh(
                "workflow",
                "run",
                "release.yml",
                "--repo",
                REPOSITORY,
                "--ref",
                "main",
                "-f",
                f"tag={arguments.tag}",
            )
        )
    elif arguments.command == "draft":
        draft(arguments.tag)
    else:
        publish(arguments.tag)


if __name__ == "__main__":
    main()

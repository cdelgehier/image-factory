"""Draft releases and publish only the assets built and tested for their tag.

Publication checks every enabled target before uploading any file. The release
stays a draft until the upload finishes, so users only see complete releases.
"""

import argparse
import json
import re
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from image_factory.artifacts import BoxMetadata, read_box_metadata, sha256, unpack_box
from image_factory.catalogue import (
    REPOSITORY,
    TAG_PATTERN,
    add_version,
    validate_asset,
    version_key,
)
from image_factory.targets import Target, enabled_targets


def gh(*arguments: str) -> str:
    """Run GitHub CLI with separate arguments and keep API errors visible."""

    return subprocess.check_output(["gh", *arguments], text=True)


def require_test_report(path: Path, minimum_tests: int = 7) -> None:
    """Require a complete passing box report before publishing its assets.

    A report with no failures can still be incomplete if tests were skipped
    or never collected. The recipe sets the minimum expected test count.
    """

    suites = list(ET.parse(path).getroot().iter("testsuite"))
    if (
        not suites
        or sum(int(suite.get("tests", "0")) for suite in suites) < minimum_tests
    ):
        raise ValueError("The report must include all box tests")
    if any(
        int(suite.get(field, "0"))
        for suite in suites
        for field in ("failures", "errors", "skipped")
    ):
        raise ValueError("All box tests must pass without skips")


def release_record(tag: str) -> dict[str, Any] | None:
    """Find a release by tag without hiding a failed GitHub request.

    A missing release returns None. An API error must stop the caller rather
    than look like a missing release and trigger another creation attempt.
    """

    # Older drafts may be on later pages, so the first API page is not enough.
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
    """Create the draft once and allow retries while it stays unpublished.

    A published release is final. Version workflow retries must never turn
    it back into a draft or replace its assets.
    """

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


def previous_catalogue(tag: str, target: Target) -> dict[str, Any] | None:
    """Read this target's history from the highest published stable version.

    The new tag must be newer because publication moves the stable download
    URL to this release. A newly added target may have no earlier catalogue.
    """

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
    # Release creation dates do not always follow version order.
    latest = max(published, key=release_version_key)
    if version_key({"version": tag[1:]}) <= release_version_key(latest):
        raise ValueError("Publish a version newer than the latest release")
    if target.catalogue_file not in {asset["name"] for asset in latest["assets"]}:
        return None
    with tempfile.TemporaryDirectory(prefix="image-factory-catalogue-") as directory:
        gh(
            "release",
            "download",
            latest["tag_name"],
            "--repo",
            REPOSITORY,
            "--pattern",
            target.catalogue_file,
            "--dir",
            directory,
        )
        return json.loads((Path(directory) / target.catalogue_file).read_text())


def validate_manifest(manifest: dict[str, Any], target: Target) -> None:
    """Require build records to describe the recipe selected for publication.

    Target-specific filenames alone cannot prove that the guest account,
    provider and architecture belong to the same image.
    """

    metadata = BoxMetadata.model_validate(manifest.get("box_metadata"))
    box = manifest.get("box")
    if not isinstance(box, dict):
        raise ValueError("Invalid box record in the build manifest")
    if (
        manifest.get("target") != target.name
        or manifest.get("architecture") != target.architecture
        or manifest.get("communicator", "ssh") != target.communicator
        or manifest.get(f"{target.communicator}_username") != target.username
        or metadata.architecture != target.architecture
        or metadata.provider != target.box_provider
        or box.get("filename") != target.box.name
    ):
        raise ValueError("The build manifest does not match the target")


def checked_assets(tag: str, head: str, target: Target) -> list[Path]:
    """Recheck one target's build records and prepare its catalogue asset.

    The manifest, report, archive and disk must agree before any upload.
    Checking the files again catches outputs changed since the build step.
    """

    manifest = json.loads(target.manifest.read_text())
    validate_manifest(manifest, target)
    if manifest["commit"] != head:
        raise ValueError("The manifest must match the release commit")
    require_test_report(target.report, target.minimum_tests)
    checksum = sha256(target.box)
    # Check the actual file as well as both records that claim its checksum.
    if (
        checksum != manifest["box"]["sha256"]
        or target.box.stat().st_size != manifest["box"]["bytes"]
        or target.box.with_suffix(".box.sha256").read_text()
        != f"{checksum}  {target.box.name}\n"
    ):
        raise ValueError("The release checksum does not match the tested box")
    with tempfile.TemporaryDirectory(prefix="image-factory-release-") as directory:
        # Inspect the packaged disk, not just the standalone build output.
        disk = unpack_box(target.box, Path(directory), target)
        if sha256(disk) != manifest["disk_sha256"]:
            raise ValueError("The manifest checksum does not match the packaged disk")
        if (
            read_box_metadata(Path(directory) / "metadata.json", target)
            != manifest["box_metadata"]
        ):
            raise ValueError("The manifest metadata does not match the box")
    catalogue = add_version(previous_catalogue(tag, target), tag, checksum, target)
    catalogue_path = target.dist / target.catalogue_file
    catalogue_path.write_text(json.dumps(catalogue, indent=2) + "\n")
    return [
        target.box,
        target.box.with_suffix(".box.sha256"),
        target.manifest,
        target.report,
        catalogue_path,
    ]


def publish(tag: str) -> None:
    """Check all targets, upload to the draft, then make the release public.

    The checkout and project version must match the tag requested by the
    workflow. Published releases are rejected before any asset can change.
    """

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tagged = subprocess.check_output(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], text=True
    ).strip()
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    if head != tagged or version != tag[1:]:
        raise ValueError(
            "The version, manifest and checkout must match the release tag"
        )
    record = release_record(tag)
    if record is None or not record["draft"]:
        raise ValueError("Publication requires an existing draft release")
    # Finish checks for every target before uploading a partial set of assets.
    assets = []
    for target in enabled_targets():
        assets.extend(checked_assets(tag, head, target))
    for asset in assets:
        validate_asset(asset.stat().st_size)
    # Replacing files is allowed only in the draft, so failed uploads can be retried.
    gh("release", "upload", tag, *map(str, assets), "--repo", REPOSITORY, "--clobber")
    # Keep this last: the stable catalogue URL must point at a complete release.
    gh("release", "edit", tag, "--repo", REPOSITORY, "--draft=false", "--latest")


def main() -> None:
    """Dispatch, draft or publish a stable tag through the Taskfile entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("dispatch", "draft", "publish"))
    parser.add_argument("tag")
    arguments = parser.parse_args()
    if not re.fullmatch(TAG_PATTERN, arguments.tag):
        parser.error("Use a stable tag such as v0.1.0")
    if arguments.command == "dispatch":
        # Use the workflow stored in the requested tag, not a newer main revision.
        gh("api", f"repos/{REPOSITORY}/git/ref/tags/{arguments.tag}")
        print(
            gh(
                "workflow",
                "run",
                "release.yml",
                "--repo",
                REPOSITORY,
                "--ref",
                arguments.tag,
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

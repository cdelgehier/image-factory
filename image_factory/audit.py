"""Check the latest public catalogue and the actual files downloaded by users."""

import json
import re
import tarfile
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from image_factory.artifacts import (
    BuildProvenance,
    PackerTarInfo,
    check_disk,
    sha256,
    unpack_box,
)
from image_factory.catalogue import REPOSITORY, TAG_PATTERN, validate_catalogue
from image_factory.catalogue_site import (
    SITE_URL,
    download_catalogue,
    read_remote_catalogue,
)
from image_factory.release import gh, require_test_report, validate_manifest
from image_factory.signatures import signature_files, verify_target
from image_factory.targets import Target, load_target

MAX_BOX_BYTES = 2**31 - 1
MAX_RECORD_BYTES = 4 * 1024**2
MAX_EXTRACTED_BYTES = 8 * 1024**3


def release_requires_signatures(tag: str) -> bool:
    """Read the tagged source to distinguish old builds from missing bundles.

    A version bump between the manifest and signing PRs may still be unsigned.
    The presence of signing code at the tag is the migration boundary.
    """

    if not re.fullmatch(TAG_PATTERN, tag):
        raise ValueError("Expected a stable release tag")
    tree = json.loads(gh("api", f"repos/{REPOSITORY}/git/trees/{tag}?recursive=1"))
    if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise ValueError(
            "Cannot determine signing policy from an incomplete source tree"
        )
    return any(
        item["path"] == "image_factory/signatures.py" and item["type"] == "blob"
        for item in tree["tree"]
    )


def download_file(url: str, destination: Path, *, limit: int) -> None:
    """Stream public files with a size bound instead of trusting HTTP headers."""

    if not url.startswith("https://"):
        raise ValueError("Release downloads require HTTPS")
    request = Request(url, headers={"User-Agent": "image-factory-audit"})
    size = 0
    with urlopen(request, timeout=60) as response, destination.open("wb") as output:
        while chunk := response.read(1024**2):
            size += len(chunk)
            if size > limit:
                raise ValueError(f"Download exceeds size limit: {destination.name}")
            output.write(chunk)
    if size == 0:
        raise ValueError(f"Empty release file: {destination.name}")


def check_archive_size(box: Path) -> None:
    """Bound disk extraction before unpacking a downloaded compressed archive."""

    size = 0
    with tarfile.open(box, tarinfo=PackerTarInfo) as archive:
        for count, member in enumerate(archive, start=1):
            size += member.size
            if count > 3 or member.size < 0 or size > MAX_EXTRACTED_BYTES:
                raise ValueError("Box archive exceeds extraction limits")


def audit_target(target: Target, tag: str, catalogue: dict, directory: Path) -> dict:
    """Verify published records, signatures and the packaged disk without a rebuild."""

    if target.publication != "release":
        raise ValueError("Cannot audit a local recipe as a public image")
    if not re.fullmatch(TAG_PATTERN, tag):
        raise ValueError("Expected a stable release tag")
    validate_catalogue(catalogue, target)
    if not catalogue["versions"] or catalogue["versions"][0]["version"] != tag[1:]:
        raise ValueError("The public catalogue must start with the latest release")
    published = target.model_copy(update={"root": directory})
    published.dist.mkdir(parents=True)
    checksum_file = published.box.with_suffix(".box.sha256")
    files = [published.box, checksum_file, published.manifest, published.report]
    signed = release_requires_signatures(tag)
    if signed:
        files.extend(signature_files(published))
    for path in files:
        url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{path.name}"
        download_file(
            url,
            path,
            limit=MAX_BOX_BYTES if path == published.box else MAX_RECORD_BYTES,
        )
    # Verify the exact tag identity before using fields from a signed manifest.
    if signed:
        verify_target(published, tag)
    manifest = json.loads(published.manifest.read_text())
    validate_manifest(manifest, published)
    if signed:
        provenance = BuildProvenance.model_validate(manifest)
        bundles = signature_files(published)
        if (
            manifest.get("schema_version") != 1
            or not provenance.build_url
            or manifest.get("signatures")
            != {
                "box": bundles[0].name,
                "manifest": bundles[1].name,
            }
        ):
            raise ValueError("Missing signed build provenance")
    checksum = sha256(published.box)
    provider = catalogue["versions"][0]["providers"][0]
    if (
        checksum != provider["checksum"]
        or checksum != manifest["box"]["sha256"]
        or published.box.stat().st_size != manifest["box"]["bytes"]
        or checksum_file.read_text() != f"{checksum}  {published.box.name}\n"
    ):
        raise ValueError("Downloaded box does not match its published checksums")
    require_test_report(published.report, target.minimum_tests)
    check_archive_size(published.box)
    disk = unpack_box(published.box, published.dist / "unpacked", published)
    info = check_disk(disk)
    if (
        sha256(disk) != manifest["disk_sha256"]
        or info["virtual-size"] != manifest["virtual_size"]
    ):
        raise ValueError("The packaged disk does not match its build manifest")
    return {
        "target": target.name,
        "version": tag[1:],
        "status": "passed",
        "signatures": "verified" if signed else "legacy_unsigned",
        "sha256": checksum,
    }


def main() -> None:
    """Audit one selected public target against the latest release and Pages."""

    target = load_target()
    record = json.loads(gh("api", f"repos/{REPOSITORY}/releases/latest"))
    tag = record["tag_name"]
    if record["draft"] or record["prerelease"] or not re.fullmatch(TAG_PATTERN, tag):
        raise ValueError("The audit needs a published stable release")
    expected = download_catalogue(tag, target)
    actual = read_remote_catalogue(f"{SITE_URL}{target.catalogue_file}")
    if actual != expected:
        raise ValueError("Public catalogue differs from the latest GitHub release")
    with tempfile.TemporaryDirectory(prefix="image-factory-audit-") as directory:
        result = audit_target(target, tag, actual, Path(directory))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

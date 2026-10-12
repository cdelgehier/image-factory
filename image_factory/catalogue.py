"""Keep a target's published box versions in one Vagrant catalogue.

Each new release includes the earlier entries, so the stable catalogue URL
still lets users request an older box version.
"""

import copy
import re
from typing import Any

from image_factory.targets import Target, load_target

REPOSITORY = "cdelgehier/image-factory"
TAG_PATTERN = r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def validate_asset(size: int) -> None:
    """Reject empty files and files at or above the 2 GiB release limit."""

    if not 0 < size < 2**31:
        raise ValueError("Release assets must be non-empty and smaller than 2 GiB")


def add_version(
    previous: dict[str, Any] | None,
    tag: str,
    checksum: str,
    target: Target | None = None,
) -> dict[str, Any]:
    """Add one release while keeping the published version history intact.

    Old entries must still match this target's expected provider URLs and
    checksums. An existing version cannot be replaced with a new build.
    """

    target = target or load_target()
    if target.publication == "local":
        raise ValueError("Cannot create a release catalogue for a local recipe")
    box_name = f"{REPOSITORY.split('/')[0]}/{target.name}"
    if not re.fullmatch(TAG_PATTERN, tag) or not re.fullmatch(
        r"[0-9a-f]{64}", checksum
    ):
        raise ValueError("Invalid release tag or SHA-256 checksum")
    # Do not change the caller's history if validation fails or it reuses the input.
    catalogue = (
        copy.deepcopy(previous)
        if previous is not None
        else {
            "name": box_name,
            "description": f"{target.display_name} for QEMU and libvirt.",
            "versions": [],
        }
    )
    if catalogue.get("name") != box_name or not isinstance(
        catalogue.get("versions"), list
    ):
        raise ValueError("Invalid previous catalogue")
    versions = catalogue["versions"]
    # Recheck old entries because the catalogue comes from a published asset.
    for entry in versions:
        if (
            not isinstance(entry, dict)
            or not re.fullmatch(TAG_PATTERN, "v" + str(entry.get("version", "")))
            or not isinstance(entry.get("providers"), list)
        ):
            raise ValueError("Invalid previous version")
        old_providers = entry["providers"]
        if not old_providers or not isinstance(old_providers[0], dict):
            raise ValueError("Invalid previous provider metadata")
        if old_providers != make_providers(
            "v" + entry["version"], old_providers[0].get("checksum", ""), target
        ):
            raise ValueError("Invalid previous provider metadata")
        if entry["version"] == tag[1:]:
            raise ValueError("The published version already exists")
    providers = make_providers(tag, checksum, target)
    versions.append({"version": tag[1:], "providers": providers})
    versions.sort(key=version_key, reverse=True)
    return catalogue


def version_key(entry: dict[str, Any]) -> tuple[int, ...]:
    """Compare stable versions numerically, so 0.10.0 comes after 0.9.0."""

    return tuple(int(part) for part in entry["version"].split("."))


def make_providers(tag: str, checksum: str, target: Target) -> list[dict[str, Any]]:
    """Point both providers at the same box in its tagged release.

    The QEMU provider accepts the libvirt archive format. Two catalogue
    entries let users choose either provider without duplicating the asset.
    """

    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid provider checksum")
    return [
        {
            "name": name,
            "architecture": target.architecture,
            "default_architecture": True,
            "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{target.box.name}",
            "checksum_type": "sha256",
            "checksum": checksum,
        }
        for name in ("libvirt", "qemu")
    ]

"""Create the cumulative Vagrant catalogue for published boxes."""

import copy
import re
from typing import Any

REPOSITORY = "cdelgehier/image-factory"
BOX_NAME = "cdelgehier/debian-13-arm64"
BOX_FILE = "debian-13-arm64.box"
TAG_PATTERN = r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def validate_asset(size: int) -> None:
    """Require a non-empty asset below the GitHub limit."""
    if not 0 < size < 2**31:
        raise ValueError("Release assets must be non-empty and smaller than 2 GiB")


def add_version(
    previous: dict[str, Any] | None, tag: str, checksum: str
) -> dict[str, Any]:
    """Add an immutable version without changing earlier entries."""
    if not re.fullmatch(TAG_PATTERN, tag) or not re.fullmatch(
        r"[0-9a-f]{64}", checksum
    ):
        raise ValueError("Invalid release tag or SHA-256 checksum")
    catalogue = (
        copy.deepcopy(previous)
        if previous is not None
        else {
            "name": BOX_NAME,
            "description": "Debian 13 ARM64 for QEMU and libvirt.",
            "versions": [],
        }
    )
    if catalogue.get("name") != BOX_NAME or not isinstance(
        catalogue.get("versions"), list
    ):
        raise ValueError("Invalid previous catalogue")
    versions = catalogue["versions"]
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
            "v" + entry["version"], old_providers[0].get("checksum", "")
        ):
            raise ValueError("Invalid previous provider metadata")
        if entry["version"] == tag[1:]:
            raise ValueError("The published version already exists")
    providers = make_providers(tag, checksum)
    versions.append({"version": tag[1:], "providers": providers})
    versions.sort(key=version_key, reverse=True)
    return catalogue


def version_key(entry: dict[str, Any]) -> tuple[int, ...]:
    """Sort stable versions using their numeric parts."""
    return tuple(int(part) for part in entry["version"].split("."))


def make_providers(tag: str, checksum: str) -> list[dict[str, Any]]:
    """Use the same immutable asset for both ARM64 provider entries."""
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid provider checksum")
    return [
        {
            "name": name,
            "architecture": "arm64",
            "default_architecture": True,
            "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{BOX_FILE}",
            "checksum_type": "sha256",
            "checksum": checksum,
        }
        for name in ("libvirt", "qemu")
    ]

"""Pin official Debian cloud images to a dated URL and checksum."""

import base64
import json
from pathlib import Path
from typing import Any, Self
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

BASE_URL = "https://cloud.debian.org/images/cloud/"
CONFIG = Path(__file__).with_name("source.pkrvars.json")


class Source(BaseModel):
    """Describe the single Debian ARM64 source used by Packer."""

    model_config = ConfigDict(extra="forbid")
    source_build: str = Field(pattern=r"^\d{8}-\d+$")
    source_url: str
    source_checksum: str = Field(pattern=r"^sha512:[0-9a-f]{128}$")

    @model_validator(mode="after")
    def check_url(self) -> Self:
        """Require the official path for the selected build."""
        expected = f"{BASE_URL}trixie/{self.source_build}/debian-13-generic-arm64-{self.source_build}.qcow2"
        if self.source_url != expected:
            raise ValueError("The source must use the dated official Debian URL")
        return self


def build_version(metadata: dict[str, Any]) -> str:
    """Read the build version only after checking the target."""
    try:
        builds = [
            item["data"]["info"]
            for item in metadata["items"]
            if item["kind"] == "Build"
        ]
        if len(builds) != 1:
            raise ValueError("Expected one Debian build")
        info = builds[0]
        expected = {
            "arch": "arm64",
            "release": "trixie",
            "release_baseid": "13",
            "vendor": "generic",
            "type": "official",
        }
        if any(info.get(key) != value for key, value in expected.items()):
            raise ValueError(
                "The metadata does not describe official Debian 13 generic ARM64"
            )
        version = info["version"]
        Source(
            source_build=version,
            source_url=f"{BASE_URL}trixie/{version}/debian-13-generic-arm64-{version}.qcow2",
            source_checksum="sha512:" + "00" * 64,
        )
        return version
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid Debian build metadata") from error


def source_from_metadata(metadata: dict[str, Any], sums: str) -> Source:
    """Cross-check the upload digest against the dated checksum list."""
    version = build_version(metadata)
    filename = f"debian-13-generic-arm64-{version}.qcow2"
    reference = f"trixie/{version}/{filename}"
    try:
        uploads = [
            item
            for item in metadata["items"]
            if item["kind"] == "Upload" and item["data"]["ref"] == reference
        ]
        if len(uploads) != 1:
            raise ValueError("Expected one official QCOW2 upload")
        digest = uploads[0]["metadata"]["annotations"]["cloud.debian.org/digest"]
        if not digest.startswith("sha512:"):
            raise ValueError("Expected a SHA-512 digest")
        encoded = digest.removeprefix("sha512:")
        checksum = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), validate=True
        ).hex()
        matches = [
            line.split()[0]
            for line in sums.splitlines()
            if len(line.split()) == 2 and line.split()[1].lstrip("*") == filename
        ]
        if matches != [checksum]:
            raise ValueError("The upload digest and checksum list do not match")
        return Source(
            source_build=version,
            source_url=BASE_URL + reference,
            source_checksum="sha512:" + checksum,
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid Debian upload metadata") from error


def download_text(url: str) -> str:
    """Read official metadata with a bounded network timeout."""
    with urlopen(url, timeout=60) as response:
        return response.read(4 * 1024 * 1024).decode()


def main() -> None:
    """Update the source file only when all checks pass."""
    metadata = json.loads(
        download_text(BASE_URL + "trixie/latest/debian-13-generic-arm64.json")
    )
    version = build_version(metadata)
    source = source_from_metadata(
        metadata, download_text(f"{BASE_URL}trixie/{version}/SHA512SUMS")
    )
    if CONFIG.exists():
        current = Source.model_validate_json(CONFIG.read_text())
        if tuple(map(int, source.source_build.split("-"))) < tuple(
            map(int, current.source_build.split("-"))
        ):
            raise ValueError("Refusing to move to an older Debian build")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(source.model_dump_json(indent=2) + "\n")
    temporary.replace(CONFIG)
    print(f"Debian source: {source.source_build}")


if __name__ == "__main__":
    main()

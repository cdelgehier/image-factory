"""Pin released Ubuntu 26.04 ARM64 images to a dated source.

The release stream selects a build, but its disk path and checksum are checked
again before saving the pin. Daily images are outside this recipe's scope.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

BASE_URL = "https://cloud-images.ubuntu.com/"
STREAM_URL = BASE_URL + "releases/streams/v1/com.ubuntu.cloud:released:download.json"
PRODUCT = "com.ubuntu.cloud:server:26.04:arm64"
FILENAME = "ubuntu-26.04-server-cloudimg-arm64.img"
CONFIG = Path(__file__).with_name("source.pkrvars.json")
MAX_METADATA_BYTES = 32 * 1024 * 1024


class Source(BaseModel):
    """Keep the Ubuntu build date, official disk URL and SHA-256 together.

    The stream provides the disk path, so it must match the expected release
    directory and filename instead of being accepted as an arbitrary URL.
    """

    model_config = ConfigDict(extra="forbid")
    source_build: str = Field(pattern=r"^\d{8}(?:\.\d+)?$")
    source_url: str
    source_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_url(self) -> Self:
        """Require a valid date and the official path for this build."""

        datetime.strptime(self.source_build.split(".")[0], "%Y%m%d")
        expected = (
            f"{BASE_URL}server/releases/resolute/release-{self.source_build}/{FILENAME}"
        )
        if self.source_url != expected:
            raise ValueError("The source must use the dated official Ubuntu URL")
        return self


class ReleaseStream(BaseModel):
    """Identify the released download stream before reading its products.

    Ubuntu uses a similar format for daily images. The stream identity keeps
    an unexpected feed from supplying this recipe's source.
    """

    content_id: Literal["com.ubuntu.cloud:released:download"]
    datatype: Literal["image-downloads"]
    format: Literal["products:1.0"]
    products: dict[str, Any]


class Product(BaseModel):
    """Require the supported Ubuntu release and architecture for this target."""

    arch: Literal["arm64"]
    os: Literal["ubuntu"]
    release: Literal["resolute"]
    version: Literal["26.04"]
    supported: Literal[True]
    versions: dict[str, Any] = Field(min_length=1)


class ReleaseBuild(BaseModel):
    """Require a release build, even when its parent stream is valid."""

    label: Literal["release"]
    items: dict[str, Any]


class Disk(BaseModel):
    """Read the downloadable disk record, excluding other image asset types."""

    ftype: Literal["disk1.img"]
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)


def build_order(build: str) -> tuple[int, ...]:
    """Compare dates and build numbers numerically, including same-day rebuilds."""

    return tuple(map(int, build.split(".")))


def latest_source(metadata: dict[str, Any]) -> Source:
    """Select the newest supported release after validating its build IDs.

    Every build ID is checked before sorting. The selected disk then passes
    the pin model, which ties its path to the expected dated release directory.
    """

    stream = ReleaseStream.model_validate(metadata)
    try:
        product = Product.model_validate(stream.products[PRODUCT])
        # Check all IDs before sorting; a malformed newer ID must not be ignored.
        for version in product.versions:
            Source(
                source_build=version,
                source_url=f"{BASE_URL}server/releases/resolute/release-{version}/{FILENAME}",
                source_checksum="sha256:" + "00" * 32,
            )
        version = max(product.versions, key=build_order)
        build = ReleaseBuild.model_validate(product.versions[version])
        disk = Disk.model_validate(build.items["disk1.img"])
        # Do not trust a valid stream to provide a safe or correct disk path.
        return Source(
            source_build=version,
            source_url=BASE_URL + disk.path,
            source_checksum="sha256:" + disk.sha256,
        )
    except KeyError as error:
        raise ValueError("Missing Ubuntu release or disk metadata") from error


def source_from_metadata(metadata: dict[str, Any], sums: str) -> Source:
    """Require the release stream and checksum list to agree on the disk.

    Exactly one row must match the expected filename. Missing, wrong or
    duplicate rows leave the current source pin unchanged in the caller.
    """

    source = latest_source(metadata)
    matches = [
        line.split()[0]
        for line in sums.splitlines()
        if len(line.split()) == 2 and line.split()[1].lstrip("*") == FILENAME
    ]
    if matches != [source.source_checksum.removeprefix("sha256:")]:
        raise ValueError("The image digest and checksum list do not match")
    return source


def download_text(url: str) -> str:
    """Read the release stream within a timeout and a fixed memory limit.

    The stream contains many releases and is larger than Debian metadata.
    Read one extra byte to detect an oversized response instead of truncating it.
    """

    with urlopen(url, timeout=60) as response:
        content = response.read(MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        raise ValueError("The Ubuntu metadata exceeds the download size limit")
    return content.decode()


def main() -> None:
    """Save the newest released source only after checking its dated checksum.

    Validation failures and older upstream results keep the accepted pin.
    The replacement happens after all network reads and source checks finish.
    """

    metadata = json.loads(download_text(STREAM_URL))
    candidate = latest_source(metadata)
    # Fetch the checksum from this build, not from a moving release alias.
    checksum_url = candidate.source_url.rsplit("/", 1)[0] + "/SHA256SUMS"
    source = source_from_metadata(metadata, download_text(checksum_url))
    if CONFIG.exists():
        current = Source.model_validate_json(CONFIG.read_text())
        if build_order(source.source_build) < build_order(current.source_build):
            raise ValueError("Refusing to move to an older Ubuntu build")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # Replace a complete file so readers never see a partly written source pin.
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(source.model_dump_json(indent=2) + "\n")
    temporary.replace(CONFIG)
    print(f"Ubuntu source: {source.source_build}")


if __name__ == "__main__":
    main()

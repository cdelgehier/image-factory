"""Run the selected recipe's updater and share the source pin format.

Distributions publish different metadata. Each updater checks its own
official source before writing a pin that the shared build tools can read.
"""

import runpy

from pydantic import BaseModel, ConfigDict, Field

from image_factory.targets import load_target


class Source(BaseModel):
    """Store the image reference used by Packer and the build manifest.

    This model checks the common file format. The recipe's updater is still
    responsible for checking the official host, path and build metadata.
    """

    model_config = ConfigDict(extra="forbid")
    source_build: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    source_url: str = Field(pattern=r"^https://[^\s]+$")
    source_checksum: str = Field(pattern=r"^sha(?:256:[0-9a-f]{64}|512:[0-9a-f]{128})$")


class BuildSource(BaseModel):
    """Record the actual media and whether it matched the reviewed source pin."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: Source
    checksum_verified: bool


def main() -> None:
    """Update one enabled recipe without assuming a distribution format."""

    target = load_target()
    if target.publication == "local":
        raise ValueError("Local recipes use manually verified source pins")
    # Run by path because recipe names can contain dots and are not package names.
    runpy.run_path(str(target.directory / "update_source.py"), run_name="__main__")


if __name__ == "__main__":
    main()

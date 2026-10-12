"""Run the source updater owned by the selected image recipe."""

import runpy

from pydantic import BaseModel, ConfigDict, Field

from image_factory.targets import load_target


class Source(BaseModel):
    """Store a pinned image URL, build identifier and checksum."""

    model_config = ConfigDict(extra="forbid")
    source_build: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    source_url: str = Field(pattern=r"^https://[^\s]+$")
    source_checksum: str = Field(pattern=r"^sha(?:256:[0-9a-f]{64}|512:[0-9a-f]{128})$")


def main() -> None:
    """Let each recipe validate its official upstream metadata."""
    target = load_target()
    runpy.run_path(str(target.directory / "update_source.py"), run_name="__main__")


if __name__ == "__main__":
    main()

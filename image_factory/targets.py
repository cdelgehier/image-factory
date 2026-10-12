"""Select enabled recipes and keep their build outputs separate."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
NAME_PATTERN = r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$"


class TargetList(BaseModel):
    """List the recipes built and tested by GitHub workflows."""

    model_config = ConfigDict(extra="forbid")
    default: str = Field(pattern=NAME_PATTERN)
    enabled: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_default(self) -> Self:
        """Require one active default and unique recipe names."""
        if self.default not in self.enabled or len(set(self.enabled)) != len(
            self.enabled
        ):
            raise ValueError("The default must be enabled and targets must be unique")
        return self


class Target(BaseModel):
    """Describe the box contract and the runtime suite of one recipe."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_PATTERN)
    root: Path
    display_name: str = Field(min_length=1)
    architecture: str = Field(pattern=r"^arm64$")
    box_provider: str = Field(pattern=r"^libvirt$")
    communicator: Literal["ssh", "winrm"] = "ssh"
    ssh_username: str | None = Field(default=None, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    winrm_username: str | None = Field(
        default=None, pattern=r"^[a-zA-Z_][a-zA-Z0-9_-]*$"
    )
    runtime_suite: str = Field(pattern=r"^[a-z]+$")
    minimum_tests: int = Field(ge=7)

    @model_validator(mode="after")
    def check_account(self) -> Self:
        """Require the account used by the selected communicator."""
        if not getattr(self, f"{self.communicator}_username"):
            raise ValueError("The communicator account must be explicit")
        return self

    @property
    def username(self) -> str:
        """Return the validated SSH or WinRM account."""
        return getattr(self, f"{self.communicator}_username")

    @property
    def directory(self) -> Path:
        """Return the source recipe directory."""
        return self.root / "targets" / self.name

    @property
    def dist(self) -> Path:
        """Return the output directory of this target."""
        return self.root / "dist" / self.name

    @property
    def box(self) -> Path:
        """Return the release box path."""
        return self.dist / f"{self.name}.box"

    @property
    def disk(self) -> Path:
        """Return the standalone Packer disk path."""
        return self.dist / "disk" / f"{self.name}.qcow2"

    @property
    def manifest(self) -> Path:
        """Return a release asset name unique to this target."""
        return self.dist / f"{self.name}-build-manifest.json"

    @property
    def report(self) -> Path:
        """Return a release test report unique to this target."""
        return self.dist / f"{self.name}-test-report.xml"

    @property
    def catalogue_file(self) -> str:
        """Keep the catalogue URL stable for this target."""
        return f"{self.name}.json"


def load_target(name: str | None = None, *, root: Path = ROOT) -> Target:
    """Load the selected target or the configured default."""
    registry = TargetList.model_validate_json((root / "targets.json").read_text())
    selected = name or os.environ.get("IMAGE_FACTORY_TARGET") or registry.default
    if selected not in registry.enabled:
        raise ValueError(f"Target is not enabled: {selected}")
    # Validate the name before using it as a path.
    TargetList(default=selected, enabled=[selected])
    values = json.loads((root / "targets" / selected / "target.json").read_text())
    return Target(name=selected, root=root, **values)


def enabled_targets(root: Path = ROOT) -> list[Target]:
    """Read every enabled target for build and release jobs."""
    registry = TargetList.model_validate_json((root / "targets.json").read_text())
    return [load_target(name, root=root) for name in registry.enabled]


def main() -> None:
    """Print target data or run the selected runtime suite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("matrix", "list", "field", "test"))
    parser.add_argument("field", nargs="?")
    parser.add_argument("--target")
    arguments = parser.parse_args()
    if arguments.command == "matrix":
        print(json.dumps({"target": [target.name for target in enabled_targets()]}))
        return
    if arguments.command == "list":
        print("\n".join(target.name for target in enabled_targets()))
        return
    target = load_target(arguments.target)
    if arguments.command == "field":
        if arguments.field not in ("name", "directory", "dist", "box", "report"):
            parser.error("Unknown target field")
        print(getattr(target, arguments.field))
        return
    suite = ROOT / "tests/box" / target.runtime_suite
    if not suite.is_dir():
        raise ValueError(f"Missing runtime suite: {target.runtime_suite}")
    target.dist.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/box/test_archive.py",
            str(suite),
            "-v",
            f"--junitxml={target.report}",
        ],
        cwd=ROOT,
        env=os.environ | {"IMAGE_FACTORY_TARGET": target.name},
        check=True,
    )


if __name__ == "__main__":
    main()

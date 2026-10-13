"""Select recipes for Taskfile commands, GitHub jobs and release assets.

The same registry drives each stage so an enabled target is built, tested
and published with its own account, files and runtime suite.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
NAME_PATTERN = r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$"
TargetName = Annotated[str, Field(pattern=NAME_PATTERN)]


class TargetList(BaseModel):
    """Separate published recipes from recipes that users build themselves."""

    model_config = ConfigDict(extra="forbid")
    default: str = Field(pattern=NAME_PATTERN)
    enabled: list[TargetName] = Field(min_length=1)
    local: list[TargetName] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_default(self) -> Self:
        """Require one active default and unique recipe names."""

        # Duplicate names would create repeated matrix jobs and release assets.
        names = self.enabled + self.local
        if self.default not in self.enabled or len(set(names)) != len(names):
            raise ValueError("The default must be enabled and targets must be unique")
        return self


class Target(BaseModel):
    """Describe the settings shared by a recipe's build, tests and release.

    The connection account is explicit because official images do not all
    use the same user. Output names keep each target's assets separate.
    """

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
    publication: Literal["release", "local"] = "release"
    accelerator: Literal["tcg", "hvf"] = "tcg"
    cpu: Literal["max", "host"] = "max"

    @model_validator(mode="after")
    def check_account(self) -> Self:
        """Require an account for the connection method used by this guest."""

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
        """Keep this target's outputs separate from other image builds."""

        return self.root / "dist" / self.name

    @property
    def box(self) -> Path:
        """Return the release box path."""

        return self.dist / f"{self.name}.box"

    @property
    def disk(self) -> Path:
        """Locate the standalone disk checked against the packaged box."""

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
        """Keep the catalogue filename stable across this target's releases."""

        return f"{self.name}.json"


def load_target(name: str | None = None, *, root: Path = ROOT) -> Target:
    """Load a registered recipe from an argument, environment or default.

    A direct argument takes priority over the Taskfile environment. Requiring
    a registered name prevents arbitrary paths from becoming build recipes.
    """

    registry = TargetList.model_validate_json((root / "targets.json").read_text())
    selected = name or os.environ.get("IMAGE_FACTORY_TARGET") or registry.default
    if selected not in registry.enabled + registry.local:
        raise ValueError(f"Target is not enabled: {selected}")
    # Reject path separators before using a registry value to find recipe files.
    TargetList(default=selected, enabled=[selected])
    values = json.loads((root / "targets" / selected / "target.json").read_text())
    target = Target(name=selected, root=root, **values)
    # A local recipe must not become public through a registry-only edit.
    publication = "local" if selected in registry.local else "release"
    if target.publication != publication:
        raise ValueError("The registry and target publication policy must match")
    return target


def enabled_targets(root: Path = ROOT) -> list[Target]:
    """Load full recipe settings for each target in the workflow registry."""

    registry = TargetList.model_validate_json((root / "targets.json").read_text())
    return [load_target(name, root=root) for name in registry.enabled]


def all_targets(root: Path = ROOT) -> list[Target]:
    """Include private builds in configuration checks, but not job matrices."""

    registry = TargetList.model_validate_json((root / "targets.json").read_text())
    return [load_target(name, root=root) for name in registry.enabled + registry.local]


def main() -> None:
    """Expose recipe settings and run the selected archive and guest checks.

    Shell tasks and GitHub matrices use this entry point instead of keeping
    their own target lists. Runtime reports use the selected target's name.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("matrix", "list", "field", "test"))
    parser.add_argument("field", nargs="?")
    parser.add_argument("--target")
    parser.add_argument(
        "--base-ref", default=os.environ.get("IMAGE_FACTORY_BASE_REF", "")
    )
    parser.add_argument(
        "--head-ref", default=os.environ.get("IMAGE_FACTORY_HEAD_REF") or "HEAD"
    )
    arguments = parser.parse_args()
    if arguments.command == "matrix":
        selected = enabled_targets()
        if arguments.base_ref and arguments.base_ref != "0" * 40:
            from image_factory.image_selection import affected_targets, changed_paths

            paths = changed_paths(arguments.base_ref, arguments.head_ref)
            selected = affected_targets(paths, all_targets())
        print(json.dumps({"target": [target.name for target in selected]}))
        return
    if arguments.command == "list":
        print("\n".join(target.name for target in all_targets()))
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
    # Use the same Python environment and target in the pytest subprocess.
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

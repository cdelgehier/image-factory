"""Keep image builds focused on the recipes changed by a pull request.

Configuration and unit checks still cover every recipe. Only expensive guest
builds use this filter, while release jobs keep the complete published matrix.
"""

import subprocess
from pathlib import Path

from image_factory.targets import ROOT, Target


def changed_paths(base: str, head: str, *, root: Path = ROOT) -> list[str]:
    """Compare a branch with its merge base, including deleted and renamed files."""

    revisions = []
    for reference in (base, head):
        revision = subprocess.check_output(
            [
                "git",
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{reference}^{{commit}}",
            ],
            cwd=root,
            text=True,
        ).strip()
        revisions.append(revision)
    # Disable rename detection so moving shared code still covers its old path.
    output = subprocess.check_output(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--name-only",
            "--no-renames",
            "-z",
            f"{revisions[0]}...{revisions[1]}",
            "--",
        ],
        cwd=root,
        text=True,
    )
    return [path for path in output.split("\0") if path]


def affected_targets(paths: list[str], targets: list[Target]) -> list[Target]:
    """Select changed public recipes and keep unknown build inputs conservative."""

    published = [target for target in targets if target.publication == "release"]
    known_names = {target.name for target in targets}
    runtime_suites = {target.runtime_suite for target in targets}
    selected: set[str] = set()
    for path in paths:
        parts = Path(path).parts
        # Publication tools do not change the guest. Code checks still cover them.
        if (
            path.endswith(".md")
            or path.startswith(("docs/", "tests/unit/"))
            or path
            in (
                ".gitignore",
                ".pre-commit-config.yaml",
                ".github/dependabot.yml",
                ".github/workflows/catalogue.yml",
                ".github/catalogue.Taskfile.yml",
                "image_factory/catalogue.py",
                "image_factory/catalogue_site.py",
                "image_factory/release.py",
                "image_factory/image_selection.py",
            )
        ):
            continue
        if len(parts) >= 2 and parts[0] == "targets" and parts[1] in known_names:
            if parts[2:4] != ("tests", "unit"):
                selected.add(parts[1])
            continue
        if (
            len(parts) >= 4
            and parts[:2] == ("tests", "box")
            and parts[2] in runtime_suites
        ):
            selected.update(
                target.name for target in published if target.runtime_suite == parts[2]
            )
            continue
        # Shared tools, runner setup and new input paths can affect every image.
        return published
    return [target for target in published if target.name in selected]

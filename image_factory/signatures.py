"""Sign and verify release files against the project's tagged CI workflow."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from image_factory.catalogue import REPOSITORY, TAG_PATTERN
from image_factory.targets import Target, load_target

OIDC_ISSUER = "https://token.actions.githubusercontent.com"


def release_identity(tag: str) -> str:
    """Use an exact workflow and tag rather than trusting an artifact's claim."""

    if not re.fullmatch(TAG_PATTERN, tag):
        raise ValueError("Expected a stable release tag")
    return (
        f"https://github.com/{REPOSITORY}/.github/workflows/release.yml@refs/tags/{tag}"
    )


def signature_files(target: Target) -> list[Path]:
    """Keep signature names fixed so manifests cannot choose arbitrary paths."""

    return [Path(str(path) + ".bundle") for path in (target.box, target.manifest)]


def verify_target(target: Target, tag: str) -> None:
    """Require both files to be signed by the release workflow at this tag."""

    identity = release_identity(tag)
    for artifact, bundle in zip(
        (target.box, target.manifest), signature_files(target), strict=True
    ):
        if not bundle.is_file():
            raise ValueError(f"Missing signature bundle: {bundle.name}")
        subprocess.run(
            [
                "cosign",
                "verify-blob",
                "--bundle",
                str(bundle),
                "--certificate-identity",
                identity,
                "--certificate-oidc-issuer",
                OIDC_ISSUER,
                str(artifact),
            ],
            check=True,
            timeout=300,
        )


def sign_target(target: Target, tag: str) -> None:
    """Sign public, tested outputs only from the matching tagged CI run."""

    if target.publication == "local":
        raise ValueError("Cannot sign a local recipe as a public release")
    release_identity(tag)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != f"refs/tags/{tag}"
    ):
        raise ValueError("Signing requires the matching tagged release workflow")
    record = json.loads(target.manifest.read_text())
    if not os.environ.get("GITHUB_SHA") or record["commit"] != os.environ["GITHUB_SHA"]:
        raise ValueError("The manifest must match the signing commit")
    bundles = signature_files(target)
    record["signatures"] = {"box": bundles[0].name, "manifest": bundles[1].name}
    # Write the bundle references before signing; changing them later invalidates it.
    target.manifest.write_text(json.dumps(record, indent=2) + "\n")
    for artifact, bundle in zip((target.box, target.manifest), bundles, strict=True):
        subprocess.run(
            ["cosign", "sign-blob", "--yes", "--bundle", str(bundle), str(artifact)],
            check=True,
            timeout=300,
        )
    verify_target(target, tag)


def main() -> None:
    """Expose the same signature checks to Taskfile and release jobs."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sign", "verify"))
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args()
    target = load_target()
    if arguments.command == "sign":
        sign_target(target, arguments.tag)
    else:
        verify_target(target, arguments.tag)


if __name__ == "__main__":
    main()

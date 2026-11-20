import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_factory import signatures
from image_factory.artifacts import sha256
from image_factory.targets import Target, load_target


def fake_cosign(arguments: list[str], *, check: bool, timeout: int) -> None:
    """Exercise file changes at the cosign boundary without issuing certificates."""

    artifact = Path(arguments[-1])
    bundle = Path(arguments[arguments.index("--bundle") + 1])
    if arguments[1] == "sign-blob":
        bundle.write_text(sha256(artifact))
    elif bundle.read_text() != sha256(artifact):
        raise subprocess.CalledProcessError(1, arguments)


@pytest.fixture
def release_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Target:
    """Create small release inputs and a tagged GitHub Actions context."""

    target = load_target().model_copy(update={"root": tmp_path})
    target.dist.mkdir(parents=True)
    target.box.write_bytes(b"tested box")
    target.manifest.write_text(json.dumps({"commit": "ab" * 20, "schema_version": 1}))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "cdelgehier/image-factory")
    monkeypatch.setenv("GITHUB_REF", "refs/tags/v0.7.0")
    monkeypatch.setenv("GITHUB_SHA", "ab" * 20)
    return target


def test_signatures_bind_both_files_to_the_tagged_release_workflow(
    release_target: Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    cosign = Mock(side_effect=fake_cosign)
    monkeypatch.setattr(signatures.subprocess, "run", cosign)
    signatures.sign_target(release_target, "v0.7.0")
    record = json.loads(release_target.manifest.read_text())
    assert record["signatures"] == {
        "box": release_target.box.name + ".bundle",
        "manifest": release_target.manifest.name + ".bundle",
    }
    assert all(path.is_file() for path in signatures.signature_files(release_target))
    verification = [
        call.args[0]
        for call in cosign.call_args_list
        if call.args[0][1] == "verify-blob"
    ]
    assert len(verification) == 2
    for command in verification:
        assert command[command.index("--certificate-identity") + 1] == (
            "https://github.com/cdelgehier/image-factory/.github/workflows/release.yml@refs/tags/v0.7.0"
        )
        assert command[command.index("--certificate-oidc-issuer") + 1] == (
            "https://token.actions.githubusercontent.com"
        )


@pytest.mark.parametrize("artifact", ["box", "manifest"])
def test_verification_rejects_a_file_changed_after_signing(
    release_target: Target, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    monkeypatch.setattr(signatures.subprocess, "run", fake_cosign)
    signatures.sign_target(release_target, "v0.7.0")
    getattr(release_target, artifact).write_bytes(b"changed")
    with pytest.raises(subprocess.CalledProcessError):
        signatures.verify_target(release_target, "v0.7.0")


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v0.8.0"])
def test_signing_rejects_a_different_workflow_ref(
    release_target: Target, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    monkeypatch.setenv("GITHUB_REF", ref)
    with pytest.raises(ValueError, match="tagged release"):
        signatures.sign_target(release_target, "v0.7.0")


def test_private_recipes_cannot_be_signed_as_public_releases(
    release_target: Target,
) -> None:
    target = release_target.model_copy(update={"publication": "local"})
    with pytest.raises(ValueError, match="local"):
        signatures.sign_target(target, "v0.7.0")


def test_verification_requires_both_signature_bundles(release_target: Target) -> None:
    with pytest.raises(ValueError, match="bundle"):
        signatures.verify_target(release_target, "v0.7.0")

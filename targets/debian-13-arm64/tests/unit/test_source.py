import base64
import json
from importlib import import_module
from pathlib import Path

import pytest

sources = import_module("targets.debian-13-arm64.update_source")
source_from_metadata = sources.source_from_metadata


@pytest.fixture
def metadata() -> dict:
    """Return a small official Debian image record."""
    return {
        "items": [
            {
                "kind": "Build",
                "data": {
                    "info": {
                        "arch": "arm64",
                        "release": "trixie",
                        "release_baseid": "13",
                        "vendor": "generic",
                        "type": "official",
                        "version": "20261001-2618",
                    }
                },
            },
            {
                "kind": "Upload",
                "data": {
                    "ref": "trixie/20261001-2618/debian-13-generic-arm64-20261001-2618.qcow2"
                },
                "metadata": {
                    "annotations": {
                        "cloud.debian.org/digest": "sha512:"
                        + base64.b64encode(bytes.fromhex("ab" * 64))
                        .decode()
                        .rstrip("=")
                    }
                },
            },
        ]
    }


def test_resolves_a_dated_image_with_its_checksum(metadata: dict) -> None:
    source = source_from_metadata(
        metadata, "ab" * 64 + "  debian-13-generic-arm64-20261001-2618.qcow2\n"
    )
    assert source.source_build == "20261001-2618"
    assert (
        source.source_url
        == "https://cloud.debian.org/images/cloud/trixie/20261001-2618/debian-13-generic-arm64-20261001-2618.qcow2"
    )
    assert source.source_checksum == "sha512:" + "ab" * 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("arch", "amd64"),
        ("release_baseid", "14"),
        ("vendor", "genericcloud"),
        ("type", "unofficial"),
        ("version", "../../evil"),
    ],
)
def test_rejects_a_different_target(metadata: dict, field: str, value: str) -> None:
    metadata["items"][0]["data"]["info"][field] = value
    with pytest.raises(ValueError):
        source_from_metadata(metadata, "")


@pytest.mark.parametrize(
    "sums",
    ["", "00" * 64 + "  debian-13-generic-arm64-20261001-2618.qcow2", "bad checksum"],
)
def test_rejects_missing_or_wrong_checksums(metadata: dict, sums: str) -> None:
    with pytest.raises(ValueError):
        source_from_metadata(metadata, sums)


def test_rejects_an_unexpected_upload_path(metadata: dict) -> None:
    metadata["items"][1]["data"]["ref"] = "https://example.com/image.qcow2"
    with pytest.raises(ValueError):
        source_from_metadata(metadata, "")


@pytest.mark.parametrize(
    "metadata", [{}, {"items": []}, {"items": "bad"}, {"items": [{}]}]
)
def test_rejects_incomplete_metadata(metadata: dict) -> None:
    with pytest.raises(ValueError):
        source_from_metadata(metadata, "")


def test_updates_the_source_only_after_checksum_validation(
    metadata: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "source.json"
    texts = {
        sources.BASE_URL + "trixie/latest/debian-13-generic-arm64.json": json.dumps(
            metadata
        ),
        sources.BASE_URL + "trixie/20261001-2618/SHA512SUMS": "ab" * 64
        + "  debian-13-generic-arm64-20261001-2618.qcow2\n",
    }
    monkeypatch.setattr(sources, "CONFIG", config)
    monkeypatch.setattr(sources, "download_text", texts.__getitem__)
    sources.main()
    assert json.loads(config.read_text())["source_build"] == "20261001-2618"
    original = config.read_bytes()
    texts[sources.BASE_URL + "trixie/20261001-2618/SHA512SUMS"] = (
        "00" * 64 + "  debian-13-generic-arm64-20261001-2618.qcow2\n"
    )
    with pytest.raises(ValueError, match="do not match"):
        sources.main()
    assert config.read_bytes() == original


def test_does_not_move_to_an_older_build(
    metadata: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "source.json"
    current = sources.Source(
        source_build="20261001-3000",
        source_url=sources.BASE_URL
        + "trixie/20261001-3000/debian-13-generic-arm64-20261001-3000.qcow2",
        source_checksum="sha512:" + "cd" * 64,
    )
    config.write_text(current.model_dump_json())
    texts = {
        sources.BASE_URL + "trixie/latest/debian-13-generic-arm64.json": json.dumps(
            metadata
        ),
        sources.BASE_URL + "trixie/20261001-2618/SHA512SUMS": "ab" * 64
        + "  debian-13-generic-arm64-20261001-2618.qcow2\n",
    }
    monkeypatch.setattr(sources, "CONFIG", config)
    monkeypatch.setattr(sources, "download_text", texts.__getitem__)
    with pytest.raises(ValueError, match="older"):
        sources.main()
    assert sources.Source.model_validate_json(config.read_text()) == current

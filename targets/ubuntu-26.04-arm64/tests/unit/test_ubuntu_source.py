import copy
import io
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

import pytest

# The recipe name contains a dot, so load its updater from its file path.
spec = spec_from_file_location(
    "ubuntu_source", Path(__file__).resolve().parents[2] / "update_source.py"
)
assert spec is not None and spec.loader is not None
sources = module_from_spec(spec)
spec.loader.exec_module(sources)
PRODUCT = "com.ubuntu.cloud:server:26.04:arm64"
FILENAME = "ubuntu-26.04-server-cloudimg-arm64.img"
IMAGE_PATH = f"server/releases/resolute/release-20260927/{FILENAME}"


@pytest.fixture
def metadata() -> dict:
    """Provide one supported release without using the live Ubuntu stream.

    Tests change this record to cover wrong products, build IDs and disk paths
    while keeping network availability outside the unit suite.
    """

    return {
        "content_id": "com.ubuntu.cloud:released:download",
        "datatype": "image-downloads",
        "format": "products:1.0",
        "products": {
            PRODUCT: {
                "arch": "arm64",
                "os": "ubuntu",
                "release": "resolute",
                "version": "26.04",
                "supported": True,
                "versions": {
                    "20260927": {
                        "label": "release",
                        "items": {
                            "disk1.img": {
                                "ftype": "disk1.img",
                                "path": IMAGE_PATH,
                                "sha256": "ab" * 32,
                                "size": 945530880,
                            }
                        },
                    }
                },
            }
        },
    }


def test_pins_the_latest_released_image_to_its_dated_url(metadata: dict) -> None:
    versions = metadata["products"][PRODUCT]["versions"]
    versions["20260918"] = copy.deepcopy(versions["20260927"])
    source = sources.source_from_metadata(metadata, "ab" * 32 + f" *{FILENAME}\n")
    assert source.source_build == "20260927"
    assert source.source_url == sources.BASE_URL + IMAGE_PATH
    assert source.source_checksum == "sha256:" + "ab" * 32


def test_selects_a_later_build_on_the_same_day(metadata: dict) -> None:
    versions = metadata["products"][PRODUCT]["versions"]
    versions["20260927.2"] = copy.deepcopy(versions["20260927"])
    versions["20260927.2"]["items"]["disk1.img"]["path"] = (
        f"server/releases/resolute/release-20260927.2/{FILENAME}"
    )
    source = sources.source_from_metadata(metadata, "ab" * 32 + f"  {FILENAME}\n")
    assert source.source_build == "20260927.2"


@pytest.mark.parametrize(
    "field,value",
    [
        ("arch", "amd64"),
        ("os", "debian"),
        ("release", "noble"),
        ("version", "24.04"),
        ("supported", False),
    ],
)
def test_rejects_metadata_for_another_target(
    metadata: dict, field: str, value: object
) -> None:
    metadata["products"][PRODUCT][field] = value
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, "ab" * 32 + f" *{FILENAME}\n")


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_id", "com.ubuntu.cloud:daily:download"),
        ("datatype", "wrong"),
        ("format", "wrong"),
    ],
)
def test_rejects_an_unexpected_stream(metadata: dict, field: str, value: str) -> None:
    metadata[field] = value
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, "")


@pytest.mark.parametrize("build", ["../../evil", "20261301", "20260927.bad"])
def test_rejects_invalid_build_identifiers(metadata: dict, build: str) -> None:
    versions = metadata["products"][PRODUCT]["versions"]
    versions[build] = versions.pop("20260927")
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, "")


def test_rejects_a_daily_build_in_the_release_stream(metadata: dict) -> None:
    metadata["products"][PRODUCT]["versions"]["20260927"]["label"] = "daily"
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, "")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ftype", "root.tar.xz"),
        ("path", "https://example.com/image.img"),
        ("path", f"server/releases/resolute/release/{FILENAME}"),
        ("path", IMAGE_PATH.replace("arm64", "amd64")),
        ("sha256", "not a checksum"),
        ("size", 0),
    ],
)
def test_rejects_an_invalid_disk_record(
    metadata: dict, field: str, value: object
) -> None:
    metadata["products"][PRODUCT]["versions"]["20260927"]["items"]["disk1.img"][
        field
    ] = value
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, "ab" * 32 + f" *{FILENAME}\n")


@pytest.mark.parametrize(
    "sums",
    [
        "",
        "bad checksum",
        "00" * 32 + f" *{FILENAME}\n",
        "ab" * 32 + " *ubuntu-26.04-server-cloudimg-amd64.img\n",
        ("ab" * 32 + f" *{FILENAME}\n") * 2,
    ],
)
def test_rejects_missing_wrong_or_duplicate_checksums(
    metadata: dict, sums: str
) -> None:
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, sums)


@pytest.mark.parametrize(
    "metadata",
    [{}, {"products": []}, {"products": {}}, {"products": {PRODUCT: {}}}],
)
def test_rejects_incomplete_metadata(metadata: dict) -> None:
    with pytest.raises(ValueError):
        sources.source_from_metadata(metadata, "")


def test_updates_the_pin_only_after_checksum_validation(
    metadata: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "source.json"
    texts = {
        sources.STREAM_URL: json.dumps(metadata),
        sources.BASE_URL + "server/releases/resolute/release-20260927/SHA256SUMS": "ab"
        * 32
        + f" *{FILENAME}\n",
    }
    monkeypatch.setattr(sources, "CONFIG", config)
    monkeypatch.setattr(sources, "download_text", texts.__getitem__)
    sources.main()
    assert json.loads(config.read_text())["source_build"] == "20260927"
    original = config.read_bytes()
    texts[sources.BASE_URL + "server/releases/resolute/release-20260927/SHA256SUMS"] = (
        "00" * 32 + f" *{FILENAME}\n"
    )
    with pytest.raises(ValueError, match="do not match"):
        sources.main()
    assert config.read_bytes() == original


@pytest.mark.parametrize("current_build", ["20261001", "20260927.1"])
def test_does_not_move_to_an_older_build(
    metadata: dict, current_build: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "source.json"
    current = sources.Source(
        source_build=current_build,
        source_url=sources.BASE_URL
        + f"server/releases/resolute/release-{current_build}/{FILENAME}",
        source_checksum="sha256:" + "cd" * 32,
    )
    config.write_text(current.model_dump_json())
    texts = {
        sources.STREAM_URL: json.dumps(metadata),
        sources.BASE_URL + "server/releases/resolute/release-20260927/SHA256SUMS": "ab"
        * 32
        + f" *{FILENAME}\n",
    }
    monkeypatch.setattr(sources, "CONFIG", config)
    monkeypatch.setattr(sources, "download_text", texts.__getitem__)
    with pytest.raises(ValueError, match="older"):
        sources.main()
    assert sources.Source.model_validate_json(config.read_text()) == current


def test_reads_a_release_stream_larger_than_four_mib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"a" * (4 * 1024 * 1024 + 1)
    monkeypatch.setattr(sources, "urlopen", Mock(return_value=io.BytesIO(content)))
    assert sources.download_text(sources.STREAM_URL) == content.decode()


def test_rejects_metadata_above_the_download_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sources, "MAX_METADATA_BYTES", 10)
    monkeypatch.setattr(sources, "urlopen", Mock(return_value=io.BytesIO(b"a" * 11)))
    with pytest.raises(ValueError, match="size limit"):
        sources.download_text(sources.STREAM_URL)

import pytest

from image_factory.catalogue import add_version, validate_asset


def test_catalogue_keeps_old_versions_and_both_providers() -> None:
    old = add_version(None, "v0.1.0", "ab" * 32)
    catalogue = add_version(old, "v0.2.0", "cd" * 32)
    assert [item["version"] for item in catalogue["versions"]] == ["0.2.0", "0.1.0"]
    providers = catalogue["versions"][0]["providers"]
    assert {item["name"] for item in providers} == {"libvirt", "qemu"}
    assert all(item["architecture"] == "arm64" for item in providers)
    assert all(item["checksum"] == "cd" * 32 for item in providers)
    assert all("/releases/download/v0.2.0/" in item["url"] for item in providers)
    assert len(old["versions"]) == 1


def test_does_not_replace_a_published_version() -> None:
    old = add_version(None, "v0.1.0", "ab" * 32)
    with pytest.raises(ValueError, match="already exists"):
        add_version(old, "v0.1.0", "cd" * 32)


@pytest.mark.parametrize(
    "tag,checksum",
    [
        ("main", "ab" * 32),
        ("v1.0", "ab" * 32),
        ("v01.2.3", "ab" * 32),
        ("v1.2.3", "bad"),
    ],
)
def test_rejects_invalid_versions_or_checksums(tag: str, checksum: str) -> None:
    with pytest.raises(ValueError):
        add_version(None, tag, checksum)


def test_rejects_a_broken_previous_catalogue() -> None:
    with pytest.raises(ValueError):
        add_version({"versions": []}, "v0.1.0", "ab" * 32)


@pytest.mark.parametrize("size", [0, 2**31, 2**31 + 1])
def test_rejects_empty_or_large_assets(size: int) -> None:
    with pytest.raises(ValueError):
        validate_asset(size)


def test_accepts_an_asset_below_two_gib() -> None:
    validate_asset(2**31 - 1)

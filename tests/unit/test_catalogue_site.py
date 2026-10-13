import io
import json
from email.message import Message
from pathlib import Path
from unittest.mock import Mock
from urllib.response import addinfourl

import pytest

from image_factory import catalogue_site
from image_factory.catalogue import add_version
from image_factory.targets import load_target


@pytest.mark.parametrize("media_type", ["application/octet-stream", "text/plain"])
def test_remote_catalogue_rejects_headers_that_vagrant_treats_as_a_box(
    media_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = Mock()
    response.headers = {"Content-Type": media_type}
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(catalogue_site, "urlopen", Mock(return_value=response))

    with pytest.raises(ValueError, match="application/json"):
        catalogue_site.read_remote_catalogue("https://example.com/debian.json")


def test_remote_catalogue_checks_the_head_request_used_by_vagrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalogue = add_version(None, "v0.4.0", "ab" * 32)
    headers = Message()
    headers["Content-Type"] = "application/json; charset=utf-8"
    response = addinfourl(
        io.BytesIO(json.dumps(catalogue).encode()),
        headers,
        "https://example.com/debian.json",
    )
    head = addinfourl(io.BytesIO(), headers, "https://example.com/debian.json")
    request = Mock(side_effect=[head, response])
    monkeypatch.setattr(catalogue_site, "urlopen", request)

    assert (
        catalogue_site.read_remote_catalogue("https://example.com/debian.json")
        == catalogue
    )
    assert request.call_args_list[0].args[0].get_method() == "HEAD"
    assert request.call_args_list[1].args[0].get_method() == "GET"


def test_site_keeps_the_cumulative_catalogue_and_copies_only_public_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = load_target()
    catalogue = add_version(None, "v0.2.0", "ab" * 32, target)
    catalogue = add_version(catalogue, "v0.4.0", "cd" * 32, target)
    release = {
        "tag_name": "v0.4.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": target.catalogue_file},
            {"name": target.box.name},
            {"name": "windows-11-arm64.json"},
        ],
    }
    monkeypatch.setattr(catalogue_site, "gh", Mock(return_value=json.dumps(release)))
    monkeypatch.setattr(catalogue_site, "enabled_targets", Mock(return_value=[target]))
    monkeypatch.setattr(
        catalogue_site, "download_catalogue", Mock(return_value=catalogue)
    )

    catalogue_site.prepare_site(tmp_path)

    assert list(tmp_path.iterdir()) == [tmp_path / target.catalogue_file]
    assert json.loads((tmp_path / target.catalogue_file).read_text()) == catalogue


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_site_rejects_an_unpublished_or_preview_release(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = {"tag_name": "v0.4.0", "draft": False, "prerelease": False}
    release[field] = True
    monkeypatch.setattr(catalogue_site, "gh", Mock(return_value=json.dumps(release)))

    with pytest.raises(ValueError, match="published stable"):
        catalogue_site.prepare_site(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_site_rejects_metadata_that_differs_from_the_deployed_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalogue = add_version(None, "v0.4.0", "ab" * 32)
    (tmp_path / "debian-13-arm64.json").write_text(json.dumps(catalogue))
    different = add_version(None, "v0.2.0", "ab" * 32)
    monkeypatch.setattr(
        catalogue_site, "read_remote_catalogue", Mock(return_value=different)
    )

    with pytest.raises(ValueError, match="published catalogue"):
        catalogue_site.verify_site(tmp_path, "https://example.com/")


@pytest.mark.parametrize("change", ["checksum", "url", "duplicate"])
def test_site_rejects_invalid_provider_records_before_writing_files(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = load_target()
    catalogue = add_version(None, "v0.4.0", "ab" * 32, target)
    if change == "duplicate":
        catalogue["versions"].append(catalogue["versions"][0])
    else:
        catalogue["versions"][0]["providers"][0][change] = "invalid"
    release = {
        "tag_name": "v0.4.0",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": target.catalogue_file}],
    }
    monkeypatch.setattr(catalogue_site, "gh", Mock(return_value=json.dumps(release)))
    monkeypatch.setattr(catalogue_site, "enabled_targets", Mock(return_value=[target]))
    monkeypatch.setattr(
        catalogue_site, "download_catalogue", Mock(return_value=catalogue)
    )

    with pytest.raises(ValueError):
        catalogue_site.prepare_site(tmp_path)
    assert list(tmp_path.iterdir()) == []

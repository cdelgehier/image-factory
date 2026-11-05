"""Serve published Vagrant catalogues through GitHub Pages.

Vagrant checks HTTP headers before reading a catalogue. GitHub release
downloads return octet-stream even when the upload record says JSON, so
Pages serves the small catalogues and Releases keeps the large box files.
"""

import argparse
import json
import re
import tempfile
from html import escape
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from image_factory.catalogue import REPOSITORY, TAG_PATTERN, validate_catalogue
from image_factory.release import gh
from image_factory.targets import Target, enabled_targets

SITE_URL = "https://cdelgehier.github.io/image-factory/"


def download_catalogue(tag: str, target: Target) -> dict[str, Any]:
    """Read one catalogue asset without downloading its box or disk."""

    with tempfile.TemporaryDirectory(prefix="image-factory-catalogue-") as directory:
        gh(
            "release",
            "download",
            tag,
            "--repo",
            REPOSITORY,
            "--pattern",
            target.catalogue_file,
            "--dir",
            directory,
        )
        return json.loads((Path(directory) / target.catalogue_file).read_text())


def prepare_site(directory: Path) -> None:
    """Copy validated catalogues from the latest complete stable release.

    Reading latest at execution time prevents a delayed workflow from
    moving the catalogue back to an older release. All metadata is checked
    before writing files, and local recipes are never copied.
    """

    record = json.loads(gh("api", f"repos/{REPOSITORY}/releases/latest"))
    tag = record["tag_name"]
    if record["draft"] or record["prerelease"] or not re.fullmatch(TAG_PATTERN, tag):
        raise ValueError("The catalogue site needs a published stable release")
    assets = {asset["name"] for asset in record["assets"]}
    catalogues = {}
    for target in enabled_targets():
        if target.catalogue_file not in assets:
            raise ValueError(f"Missing published catalogue: {target.catalogue_file}")
        catalogue = download_catalogue(tag, target)
        validate_catalogue(catalogue, target)
        if not catalogue["versions"] or catalogue["versions"][0]["version"] != tag[1:]:
            raise ValueError("The catalogue must include the latest release first")
        catalogues[target.catalogue_file] = catalogue
    directory.mkdir(parents=True, exist_ok=True)
    for filename, catalogue in catalogues.items():
        (directory / filename).write_text(json.dumps(catalogue, indent=2) + "\n")
    (directory / "index.html").write_text(render_index(catalogues))


def render_index(catalogues: dict[str, dict[str, Any]]) -> str:
    """List validated release assets and measure browser download clicks.

    The JSON catalogues stay unchanged for Vagrant. Only the HTML page
    loads the tracker; direct CLI downloads do not run JavaScript.
    """

    cards = []
    for filename, catalogue in sorted(catalogues.items()):
        latest = catalogue["versions"][0]
        provider = latest["providers"][0]
        name = escape(catalogue["name"])
        version = escape(latest["version"])
        architecture = escape(provider["architecture"])
        download = (
            f'<a href="{escape(provider["url"])}" target="_blank" rel="noopener noreferrer" '
            f'data-umami-event="download" data-umami-event-image="{name}" '
            f'data-umami-event-version="{version}" '
            f'data-umami-event-architecture="{architecture}" '
            'data-umami-event-format="box">Download .box</a>'
        )
        cards.append(f"""<article>
<h2>{name}</h2>
<p>{escape(catalogue.get("description", ""))}</p>
<p>Version {version} · {architecture} · QEMU / libvirt</p>
<p class="downloads">{download}</p>
<p><a href="{escape(filename)}">Vagrant JSON catalogue</a> ·
<a href="https://github.com/{REPOSITORY}/releases/tag/v{version}">Release notes</a></p>
<details><summary>SHA-256 of the .box archive</summary>
<code>{escape(provider["checksum"])}</code></details>
</article>""")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="description" content="Tested ARM64 images for QEMU and Vagrant.">
<title>Image Factory — QEMU images</title>
<script defer src="https://stats.jackruby.qzz.io/script.js"
  data-website-id="b8aa4ed2-e140-4b1a-b748-0bcb62f2a657"
  data-domains="cdelgehier.github.io" data-do-not-track="true"
  data-exclude-search="true" data-exclude-hash="true"></script>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
body {{ max-width: 58rem; margin: auto; padding: 2rem 1rem; line-height: 1.6; }}
article {{ border: 1px solid #8888; border-radius: .5rem; padding: 1.5rem; margin: 1.5rem 0; }}
a {{ color: light-dark(#174ea6, #9ec5ff); }}
code {{ overflow-wrap: anywhere; }}
footer {{ margin-top: 2rem; font-size: .9rem; }}
</style>
</head>
<body>
<header><h1>Image Factory</h1>
<p>Tested ARM64 images for QEMU, libvirt and Vagrant.</p>
<p><a href="https://github.com/{REPOSITORY}">Source code and installation instructions</a></p></header>
<main>{"".join(cards)}</main>
<footer>Visits and download clicks are measured with self-hosted Umami, without cookies.
Browser Do Not Track is respected. Direct Vagrant and command-line downloads are not measured.</footer>
</body>
</html>
"""


def read_remote_catalogue(url: str) -> dict[str, Any]:
    """Check the public HTTP response as Vagrant does, then read its JSON.

    Checking the upload API is not enough: redirects and download storage
    can change the content type seen by a normal Vagrant client.
    """

    for method in ("HEAD", "GET"):
        request = Request(url, headers={"Accept": "application/json"}, method=method)
        with urlopen(request, timeout=30) as response:
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            if media_type.strip().lower() != "application/json":
                raise ValueError(f"Vagrant needs application/json at {url}")
            if method == "GET":
                return json.load(response)
    raise ValueError("No catalogue response")


def verify_site(directory: Path, url: str = SITE_URL) -> None:
    """Require the public catalogues to match the deployed release history."""

    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("No catalogues to verify")
    for path in paths:
        expected = json.loads(path.read_text())
        actual = read_remote_catalogue(f"{url.rstrip('/')}/{path.name}")
        if actual != expected:
            raise ValueError(f"The published catalogue differs: {path.name}")


def main() -> None:
    """Prepare or check the catalogue site through its Taskfile tasks."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--directory", type=Path, default=Path(".build/catalogue"))
    parser.add_argument("--url", default=SITE_URL)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        prepare_site(arguments.directory)
    else:
        verify_site(arguments.directory, arguments.url)


if __name__ == "__main__":
    main()

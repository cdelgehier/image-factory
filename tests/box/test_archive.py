"""Compare the release records with the files users will download.

The standalone disk is not the published box. Extracting the archive proves
that its disk and metadata still match the build manifest.
"""

import json
import tempfile
from pathlib import Path

from image_factory.artifacts import check_disk, read_box_metadata, sha256, unpack_box
from image_factory.targets import load_target


def test_final_box_matches_the_manifest_and_disk() -> None:
    target = load_target()
    manifest = json.loads(target.manifest.read_text())
    box = target.box
    assert manifest["target"] == target.name
    assert manifest["box"]["filename"] == box.name
    assert manifest["box"]["bytes"] == box.stat().st_size
    assert sha256(box) == manifest["box"]["sha256"]
    assert box.with_suffix(".box.sha256").read_text() == f"{sha256(box)}  {box.name}\n"
    with tempfile.TemporaryDirectory(prefix="image-factory-box-") as directory:
        disk = unpack_box(box, Path(directory), target)
        assert check_disk(disk)["virtual-size"] == manifest["virtual_size"]
        assert sha256(disk) == manifest["disk_sha256"]


def test_box_metadata_matches_the_recipe_manifest_and_disk() -> None:
    target = load_target()
    manifest = json.loads(target.manifest.read_text())
    with tempfile.TemporaryDirectory(prefix="image-factory-metadata-") as directory:
        destination = Path(directory)
        disk = unpack_box(target.box, destination, target)
        metadata = read_box_metadata(destination / "metadata.json", target)
        assert metadata == manifest["box_metadata"]
        assert metadata["provider"] == target.box_provider
        assert (
            metadata["architecture"] == target.architecture == manifest["architecture"]
        )
        # Ask QEMU about the disk instead of trusting the archive's format claim.
        assert metadata["disks"] == [
            {"path": disk.name, "format": check_disk(disk)["format"]}
        ]
        assert manifest["communicator"] == target.communicator
        assert manifest[f"{target.communicator}_username"] == target.username

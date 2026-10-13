"""Check that guest cleanup does not allocate the whole virtual disk on the host."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RECIPE = Path(__file__).resolve().parents[2]


def test_zero_fill_reclaims_deleted_blocks_and_preserves_other_data(
    tmp_path: Path,
) -> None:
    for tool in ("qemu-img", "qemu-io"):
        if shutil.which(tool) is None:
            pytest.skip(f"The disk check requires {tool}")
    template = (RECIPE / "build.pkr.hcl").read_text()
    drive = re.search(r'\["-drive", "if=none,id=disk0,(.*?)"\]', template)
    assert drive is not None
    options = dict(item.split("=", 1) for item in drive.group(1).split(","))
    image = tmp_path / "disk.qcow2"
    subprocess.run(
        ["qemu-img", "create", "-q", "-f", options["format"], str(image), "256M"],
        check=True,
        timeout=10,
    )
    # Use the recipe's block options and ordinary writes, as PowerShell does.
    block_options = (
        f"driver={options['format']},file.driver=file,file.filename={image},"
        f"detect-zeroes={options.get('detect-zeroes', 'off')},"
        f"discard={options.get('discard', 'ignore')}"
    )
    commands = ["write -q -P 42 0 32M", "write -q -P 21 192M 4M"]
    commands += [f"write -q -P 0 {offset}M 4M" for offset in range(0, 128, 4)]
    arguments = []
    for command in commands:
        arguments.extend(["-c", command])
    subprocess.run(
        ["qemu-io", "--image-opts", *arguments, block_options],
        check=True,
        capture_output=True,
        timeout=30,
    )
    read = subprocess.run(
        [
            "qemu-io",
            "-r",
            "-c",
            "read -P 0 0 128M",
            "-c",
            "read -P 21 192M 4M",
            str(image),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "Pattern verification failed" not in read.stdout
    subprocess.run(
        ["qemu-img", "check", str(image)], check=True, capture_output=True, timeout=10
    )
    info = json.loads(
        subprocess.check_output(
            ["qemu-img", "info", "--output=json", str(image)], text=True, timeout=10
        )
    )
    assert info["virtual-size"] == 256 * 1024 * 1024
    assert "backing-filename" not in info
    # Only the retained 4 MiB of data and QCOW2 metadata should occupy space.
    assert info["actual-size"] < 8 * 1024 * 1024

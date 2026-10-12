# Add a Rocky Linux ARM64 image

In this tutorial, we will create a Rocky Linux 10.2 ARM64 recipe from an
official GenericCloud image and submit it for build and guest tests on
GitHub Actions. Along the way, we will pin a source, prepare the SSH account
and add checks for the new guest.

The example uses Rocky Linux 10.2, build `20260525.0`, with the `rocky` account.
Rocky Linux is a tutorial example; the [main README](../../README.md) lists
the available targets.

## Before you begin

Use a checkout of Image Factory with Git, Python 3.14, uv, Task, Packer,
ShellCheck, shfmt and actionlint installed. You need permission to open a
pull request in the repository. Run the commands from the repository root.
GitHub's `macos-26` ARM64 runners build and test the image with QEMU TCG.

## 1. Create the recipe

Create a branch and copy the Debian recipe:

```sh
git switch -c feat/rocky-10.2-arm64
cp -R targets/debian-13-arm64 targets/rocky-10.2-arm64
rm targets/rocky-10.2-arm64/tests/unit/test_source.py
```

The new directory contains the Packer template, build scripts and bundled
Vagrantfile. We removed the Debian source tests because Rocky publishes
different source metadata.

Replace `targets/rocky-10.2-arm64/target.json` with:

```json
{
  "display_name": "Rocky Linux 10.2 ARM64",
  "architecture": "arm64",
  "box_provider": "libvirt",
  "communicator": "ssh",
  "ssh_username": "rocky",
  "runtime_suite": "linux",
  "minimum_tests": 7
}
```

In `build.pkr.hcl`, replace every `debian-13-arm64` with `rocky-10.2-arm64`
and every remaining `debian` with `rocky`. Set `disk_size` to `"32G"`.
In the bundled `Vagrantfile`, set `config.ssh.username = "rocky"`.
Keep `config.ssh.insert_key = true` and the cloud-init user data.

The Packer source is now `source.qemu.rocky`, and both output paths contain
`rocky-10.2-arm64`. Keep TCG, UEFI, CPU model `max`, disk compression and
`use_backing_file = false`.

## 2. Pin the official image

Open the [official ARM64 image directory](https://dl.rockylinux.org/pub/rocky/10.2/images/aarch64/).
Rocky Linux publishes the [GenericCloud image](https://dl.rockylinux.org/pub/rocky/10.2/images/aarch64/Rocky-10-GenericCloud-Base-10.2-20260525.0.aarch64.qcow2)
and its [checksum file](https://dl.rockylinux.org/pub/rocky/10.2/images/aarch64/Rocky-10-GenericCloud-Base-10.2-20260525.0.aarch64.qcow2.CHECKSUM)
in this directory. Use the dated filename rather than the `latest` alias.

Replace `targets/rocky-10.2-arm64/source.pkrvars.json` with:

```json
{
  "source_build": "10.2-20260525.0",
  "source_url": "https://dl.rockylinux.org/pub/rocky/10.2/images/aarch64/Rocky-10-GenericCloud-Base-10.2-20260525.0.aarch64.qcow2",
  "source_checksum": "sha256:457c8375e19496f43a25c4a6169fa11237536c53cef6f85a20ea3c5a751aa0f5"
}
```

The checksum file names this exact image and contains the same SHA-256.
Packer will verify the downloaded disk against this digest.

Replace `targets/rocky-10.2-arm64/update_source.py` with:

```python
"""Pin official Rocky Linux 10.2 ARM64 images to a dated source.

The directory is used to find a build. The saved URL and checksum refer to
that exact build so Packer does not follow a moving latest alias.
"""

import re
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from image_factory.sources import Source

BASE_URL = "https://dl.rockylinux.org/pub/rocky/10.2/images/aarch64/"
CONFIG = Path(__file__).with_name("source.pkrvars.json")
IMAGE = re.compile(r"^Rocky-10-GenericCloud-Base-(10\.2)-(\d{8}\.\d+)\.aarch64\.qcow2$")


def build_key(filename: str) -> tuple[int, int, int]:
    """Validate a dated image name and compare its numeric build parts."""

    match = IMAGE.fullmatch(filename)
    if match is None:
        raise ValueError("Expected a dated Rocky 10.2 ARM64 GenericCloud image")
    version, build = match.groups()
    date, revision = build.split(".")
    datetime.strptime(date, "%Y%m%d")
    # Numeric ordering keeps build .10 after .2 on the same date.
    return int(version.split(".")[1]), int(date), int(revision)


def latest_image(listing: str) -> str:
    """Select the newest ARM64 Base image from the pinned Rocky release.

    The filename pattern excludes latest aliases and other image variants.
    Staying on 10.2 keeps the source in line with the guest version checks.
    """

    filenames = {
        name for name in re.findall(r'href="([^"]+)"', listing) if IMAGE.fullmatch(name)
    }
    if not filenames:
        raise ValueError("No dated Rocky 10.2 ARM64 image found")
    return max(filenames, key=build_key)


def source_from_checksum(filename: str, sums: str) -> Source:
    """Require one SHA-256 entry for the exact selected image.

    Missing or duplicate rows cannot provide a clear source pin. Check the
    filename too, even when this function is called without directory selection.
    """

    build_key(filename)
    checksums = re.findall(
        rf"^SHA256 \({re.escape(filename)}\) = ([0-9a-f]{{64}})$",
        sums,
        re.MULTILINE,
    )
    if len(checksums) != 1:
        raise ValueError("Expected one matching SHA-256 checksum")
    match = IMAGE.fullmatch(filename)
    assert match is not None
    return Source(
        source_build="-".join(match.groups()),
        source_url=BASE_URL + filename,
        source_checksum="sha256:" + checksums[0],
    )


def download_text(url: str) -> str:
    """Read metadata within a timeout and a fixed memory limit."""

    with urlopen(url, timeout=30) as response:
        # One extra byte detects oversized metadata instead of accepting truncation.
        content = response.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError("Metadata exceeds the size limit")
    return content.decode()


def main() -> None:
    """Replace the source pin only after its name and checksum pass checks.

    Validation failures or an older upstream build leave the current pin in
    place. All network reads finish before the replacement file is written.
    """

    filename = latest_image(download_text(BASE_URL))
    source = source_from_checksum(
        filename, download_text(BASE_URL + filename + ".CHECKSUM")
    )
    current = Source.model_validate_json(CONFIG.read_text())
    current_filename = current.source_url.removeprefix(BASE_URL)
    if current.source_url != BASE_URL + current_filename:
        raise ValueError("Unexpected current source URL")
    if build_key(filename) < build_key(current_filename):
        raise ValueError("Refusing to move to an older Rocky build")
    # Replace a complete file so readers never see a partly written source pin.
    temporary = CONFIG.with_suffix(".tmp")
    temporary.write_text(source.model_dump_json(indent=2) + "\n")
    temporary.replace(CONFIG)
    print(f"Rocky source: {source.source_build}")


if __name__ == "__main__":
    main()
```

The updater accepts dated Rocky 10.2 ARM64 Base images and reads the checksum
for the selected filename. It rejects an older build before changing the pin.

## 3. Prepare the Rocky guest

Replace `targets/rocky-10.2-arm64/user-data.pkrtpl` with:

```yaml
#cloud-config
users:
  - name: rocky
    groups: [wheel]
    shell: /bin/bash
    lock_passwd: true
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ${build_key}
ssh_pwauth: false
disable_root: true
package_update: true
packages:
  - openssh-server
  - sudo
  - python3
  - rsync
  - qemu-guest-agent
  - iputils
```

Notice the `wheel` group and the `iputils` package. These replace the Debian
group and ping package names.

Replace `targets/rocky-10.2-arm64/provision.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

cloud-init status --wait
systemctl enable sshd qemu-guest-agent
install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-image-factory.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF
cat >/etc/cloud/cloud.cfg.d/99-image-factory.cfg <<'EOF'
system_info:
  default_user:
    name: rocky
    lock_passwd: true
    gecos: Rocky Linux
    groups: [wheel]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    shell: /bin/bash
EOF
sshd -t
sudo -u rocky sudo -n true
python3 --version
rsync --version
```

Packer can now connect as `rocky`. Future Vagrant clones also keep this account.
The SSH service is named `sshd` on Rocky.

## 4. Clean and compact the guest

Replace `targets/rocky-10.2-arm64/cleanup.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

install -d -o rocky -g rocky -m 0700 /home/rocky/.ssh
install -o rocky -g rocky -m 0600 /tmp/image-factory-vagrant.pub /home/rocky/.ssh/authorized_keys
restorecon -R /home/rocky/.ssh
rm -f /root/.ssh/authorized_keys
dnf clean all
rm -rf /var/cache/dnf/*
cloud-init clean --logs --machine-id --seed
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed
rm -f /home/rocky/.bash_history /root/.bash_history
python3 /tmp/image-factory-zero-free-space.py
find /tmp /var/tmp -mindepth 1 -delete
find /var/log -type f -exec truncate -s 0 {} +
sync
shutdown -P now
```

`restorecon` sets the SELinux labels for the SSH files. Keep SELinux enabled.
The shared helper fills free space with zeros and removes its temporary file;
Packer compresses the disk after shutdown.

## 5. Define the guest checks

Replace `targets/rocky-10.2-arm64/guest.json` with:

```json
{
  "distribution": "rocky",
  "version": "10.2",
  "packages": [
    "openssh-server", "sudo", "python3", "rsync", "qemu-guest-agent", "cloud-init"
  ],
  "services": ["sshd", "qemu-guest-agent"]
}
```

The shared Linux suite reads these expectations. It also checks the account,
ARM64 architecture, cloud-init, key replacement, rsync, restart and separate
clone identities. Archive tests check provider metadata, disk integrity and
the absence of a backing file.

Create `targets/rocky-10.2-arm64/tests/unit/test_rocky_source.py` with:

```python
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# The target name contains a dot, so import the updater by file path.
spec = spec_from_file_location(
    "rocky_source", Path(__file__).resolve().parents[2] / "update_source.py"
)
assert spec is not None and spec.loader is not None
sources = module_from_spec(spec)
spec.loader.exec_module(sources)
FILENAME = "Rocky-10-GenericCloud-Base-10.2-20260525.0.aarch64.qcow2"
SUMS = f"SHA256 ({FILENAME}) = {'ab' * 32}\n"


def test_selects_a_dated_arm64_base_image() -> None:
    listing = (
        f'<a href="{FILENAME}">image</a>'
        '<a href="Rocky-10-GenericCloud-Base.latest.aarch64.qcow2">latest</a>'
        '<a href="Rocky-10-GenericCloud-Base-10.2-20260524.0.aarch64.qcow2">older</a>'
    )
    assert sources.latest_image(listing) == FILENAME
    source = sources.source_from_checksum(FILENAME, SUMS)
    assert source.source_build == "10.2-20260525.0"
    assert source.source_url == sources.BASE_URL + FILENAME
    assert source.source_checksum == "sha256:" + "ab" * 32


@pytest.mark.parametrize("sums", ["", "bad", SUMS * 2, SUMS.replace("ab", "xx")])
def test_rejects_missing_invalid_or_duplicate_checksums(sums: str) -> None:
    with pytest.raises(ValueError):
        sources.source_from_checksum(FILENAME, sums)


@pytest.mark.parametrize(
    "filename",
    [
        FILENAME.replace("aarch64", "x86_64"),
        FILENAME.replace("Base", "LVM"),
        FILENAME.replace("20260525", "20261301"),
    ],
)
def test_rejects_another_image_or_an_invalid_date(filename: str) -> None:
    with pytest.raises(ValueError):
        sources.source_from_checksum(filename, SUMS)


def test_keeps_the_pin_when_an_older_build_is_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "source.json"
    newer = FILENAME.replace("20260525", "20260601")
    current = sources.source_from_checksum(newer, SUMS.replace(FILENAME, newer))
    config.write_text(current.model_dump_json())
    original = config.read_bytes()
    texts = {
        sources.BASE_URL: f'<a href="{FILENAME}">image</a>',
        sources.BASE_URL + FILENAME + ".CHECKSUM": SUMS,
    }
    monkeypatch.setattr(sources, "CONFIG", config)
    monkeypatch.setattr(sources, "download_text", texts.__getitem__)
    with pytest.raises(ValueError, match="older"):
        sources.main()
    assert config.read_bytes() == original
```

Run the new unit tests:

```sh
uv run --locked pytest targets/rocky-10.2-arm64/tests/unit -v
```

All nine cases should pass. No guest needs to start for these source tests.

## 6. Register and validate the target

Add `rocky-10.2-arm64` to the `enabled` list in `targets.json`:

```json
{
  "default": "debian-13-arm64",
  "enabled": ["debian-13-arm64", "ubuntu-26.04-arm64", "rocky-10.2-arm64"]
}
```

The updater stays on release 10.2, so the guest version and source pin remain
aligned. Check the registered recipe:

```sh
task sources:update TARGET=rocky-10.2-arm64
task fmt:fix
task ci
task targets:matrix
```

The updater should report a dated Rocky 10.2 source build.
Packer should report a valid configuration for each enabled target. The matrix
now includes `rocky-10.2-arm64`, so GitHub will build it without a workflow change.

## 7. Write the usage guide

Replace the copied `targets/rocky-10.2-arm64/README.md` with a Rocky guide.
Use the [Debian guide](../../targets/debian-13-arm64/README.md) as the layout.
Set the title to `Rocky Linux 10.2 ARM64`, the account to `rocky`, the disk size
to 32 GiB, the box to `cdelgehier/rocky-10.2-arm64` and the catalogue filename
to `rocky-10.2-arm64.json`. Describe the official GenericCloud source and SHA-256.
Keep the complete QEMU HVF examples, rsync settings and separate SSH port ranges.

Add a Rocky row with a link to this guide in the main README's target table.
The catalogue URL becomes usable after a release containing Rocky is published.

## 8. Run the build and guest tests on GitHub

Commit the recipe and open a pull request:

```sh
git add targets/rocky-10.2-arm64 targets.json README.md
git commit -m "feat: add Rocky Linux 10.2 ARM64 boxes"
git push -u origin feat/rocky-10.2-arm64
gh pr create --title "feat: add Rocky Linux 10.2 ARM64 boxes" --body "Build and test Rocky Linux 10.2 ARM64 from the official GenericCloud image."
```

In the pull request, open **Checks**, then **Box tests (rocky-10.2-arm64)**.
The job runs `task box:build TARGET=rocky-10.2-arm64` followed by
`task test:box TARGET=rocky-10.2-arm64`. Wait for both code and box checks to pass.

The job builds the standalone disk and box, boots two Vagrant clones and checks
the guest behavior. Its diagnostic artifact contains the Packer and guest logs,
build manifest, checksum and test report. The tests remove their Vagrant
machines and imported box after success or failure.

Your branch now contains a complete Rocky recipe, its source updater and tests,
and a usage guide. The GitHub checks show whether the final box is ready to use.

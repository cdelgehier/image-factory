# Image Factory

ARM64 Vagrant boxes, built with Packer and tested on GitHub Actions.
Debian 13 and Ubuntu 26.04 LTS use official ARM64 cloud images. Each recipe
stores its dated URL, build ID and checksum in `source.pkrvars.json`.

| Target | Source | SSH account | Source checksum |
| --- | --- | --- | --- |
| `debian-13-arm64` (default) | Debian 13 generic | `debian` | SHA-512 |
| `ubuntu-26.04-arm64` | Ubuntu Server 26.04 LTS (Resolute Raccoon) | `ubuntu` | SHA-256 |

Both boxes include SSH, passwordless sudo,
Python 3, rsync, cloud-init and the QEMU guest agent. Vagrant replaces the
standard public key when it starts a new machine.

## Use a box on Apple Silicon

Install [Vagrant](https://developer.hashicorp.com/vagrant/install), QEMU and
the tested provider version:

```sh
brew install qemu
vagrant plugin install vagrant-qemu --plugin-version 0.6.3
```

Create a `Vagrantfile`:

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "cdelgehier/debian-13-arm64"
  config.vm.box_url = "https://github.com/cdelgehier/image-factory/releases/latest/download/debian-13-arm64.json"
  config.vm.box_architecture = "arm64"
  config.ssh.username = "debian"
  config.ssh.insert_key = true
  config.vm.synced_folder ".", "/vagrant", type: "rsync"
  config.vm.provider "qemu" do |qemu|
    qemu.arch = "aarch64"
    qemu.machine = "virt,accel=hvf"
    qemu.cpu = "host"
    qemu.memory = "2G"
    qemu.ssh_auto_correct = true
  end
end
```

For Ubuntu, set `config.vm.box` to `cdelgehier/ubuntu-26.04-arm64`, use
`ubuntu-26.04-arm64.json` in the catalogue URL and set `config.ssh.username`
to `ubuntu`. The other settings stay the same.

```sh
vagrant up --provider=qemu
vagrant ssh
vagrant rsync
vagrant halt
vagrant destroy
```

For several machines, set different `qemu.ssh_port` values and give each
machine a separate `config.vm.usable_port_range` for automatic port correction.

The stable catalogue URL becomes available after the first published release.
It lists all published versions with `arm64` entries for `libvirt` and `qemu`.
Both entries use the same box file and SHA-256 checksum. To pin a version, set
`config.vm.box_version` to a release version such as `0.1.0`.

## GitHub workflows

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| Checks | Pull request or manual run | Check Python, shell scripts, Packer and workflows |
| Image tests | Pull request or manual run | Build the final box and test two Vagrant clones |
| Version | Push to `main` | Run checks and image tests, then use Commitizen to create a tag, changelog and draft |
| Release | Manual run with a tag | Build and test the exact tag, then publish its draft |
| Source updates | Monday at 06:00 UTC or manual run | Open or update a source PR for each enabled target and start its checks |

Image jobs use GitHub-hosted `macos-26` ARM64 runners, UEFI, QEMU TCG and CPU
model `max`. These runners do not support nested virtualization. The HVF
settings in the usage example are for physical Apple Silicon machines;
CI tests TCG operation.

Image tests, source updates and release builds read their job matrices from
`targets.json`. Each enabled target gets its own build job and diagnostic
artifact. Publication waits for every target to pass.

Source update runs explicitly dispatch the checks because PRs created with
`GITHUB_TOKEN` do not trigger normal pull request workflows. Each dispatched
workflow reports its result on the source PR commit. Enable **Allow GitHub
Actions to create and approve pull requests** in the repository's Actions
settings. Dependency updates use a separate Dependabot configuration.

## Build and test tasks

The workflows call the same Taskfile commands:

```sh
task ci
task sources:update
task box:build
task test:box
task box:build TARGET=ubuntu-26.04-arm64
task test:box TARGET=ubuntu-26.04-arm64
task release TAG=v0.1.0
```

Commands use the default target from `targets.json`. To select another enabled
target, pass `TARGET=<target-name>` to `box:build`, `test:box` or `sources:update`.
`task ci` validates every enabled Packer template. Releases include every enabled
target from the tagged commit.

Python tools use Python 3.14 and the committed `uv.lock`. Packer QEMU and
Vagrant plugins are pinned to `1.1.7`; `vagrant-qemu` is pinned to `0.6.3`.
The workflow installs the system tools and records build tool versions in
the manifest.

Packer creates `dist/<target>/disk/<target>.qcow2` and
`dist/<target>/<target>.box`. The disk has no backing file. The box
uses the libvirt disk-list format supported by the pinned QEMU provider.
Build keys, cloud-init state, machine identity, SSH host keys, package caches
and temporary files are removed before packaging.

The cleanup writes zeros to free space, flushes the writes and removes the
temporary zero file before shutdown. Only a full filesystem is an accepted
write error. Packer then compresses the QCOW2 disk.

The test suite checks the archive, provider metadata, checksums and QCOW2
integrity. It imports the final box through Vagrant and checks the OS,
architecture, account, packages, sudo, cloud-init and key replacement.
It also checks rsync, shutdown and restart, separate machine IDs and SSH
host keys, and communication through QEMU socket networking. The listener
starts before the connector. The tests destroy their own machines and remove
their own box after success or failure. Workflow artifacts keep diagnostic logs.

Metadata tests reject missing or malformed fields, an incorrect provider or
architecture, extra disks, an incorrect disk path or format, and unexpected
archive members. The final archive metadata must match the recipe, manifest
and QCOW2 disk.

## Image recipes

The repository keeps OS details inside each target directory:

```text
targets.json
targets/
  debian-13-arm64/
    target.json
    source.pkrvars.json
    build.pkr.hcl
    build.sh
    validate.sh
    user-data.pkrtpl
    Vagrantfile
    provision.sh
    cleanup.sh
    update_source.py
    guest.json
    tests/unit/test_source.py
  ubuntu-26.04-arm64/        # Same recipe files, with Ubuntu source tests
image_factory/              # Target selection, archive checks and publication
scripts/                    # Build commands and shared zero-fill helper
tests/box/test_archive.py    # Common box and metadata tests
tests/box/linux/             # Linux guest tests
```

`target.json` defines the display name, architecture, box provider, communicator,
account, runtime suite and minimum test count. `guest.json` contains Linux distribution,
version, package and service expectations. The recipe owns its source updater,
build and validation commands, template and provisioning files. Shared commands
have no distribution name.

Debian and Ubuntu use the shared `linux` runtime suite. Ubuntu's source updater
reads Canonical's released SimpleStreams metadata and checks its SHA-256 digest
against the dated `SHA256SUMS` file. It rejects daily images, another release or
architecture, unexpected URLs, invalid checksums and older build IDs.

For Windows 11 ARM64, add `targets/windows-11-arm64/` with its own
installation template, PowerShell preparation and `windows` runtime suite
under `tests/box/windows/`. Set `communicator` to `winrm` and define
`winrm_username` when the recipe uses WinRM. Windows tests must cover its
communicator, machine identity, file transfer and restart. Linux cloud-init and cleanup scripts stay
in Linux recipes.

Enable a recipe in `targets.json` when its build and tests are ready. The job
matrices then include it without a workflow change. Outputs stay in
`dist/<target>/`, with one stable Vagrant catalogue per target.

## Releases

Use Conventional Commits. Commitizen owns SemVer tags and the changelog.
After checks on `main` pass, the Version workflow creates a draft release.
The Release workflow accepts a stable tag such as `v0.1.0` and publishes only
after all box tests pass. `task release TAG=v0.1.0` dispatches this workflow
through an authenticated GitHub CLI.

The task runs the workflow stored in the requested tag. In the Actions UI,
select that tag as the workflow ref and enter the same tag as the input.

Each published release contains these files for every enabled target:

- `<target>.box`
- `<target>.box.sha256`
- `<target>-build-manifest.json`
- `<target>-test-report.xml`
- `<target>.json`, the cumulative Vagrant catalogue

Files must be smaller than 2 GiB. Publication rejects an existing published
version and a tag older than the latest release. Failed publication may be
retried while the release is still a draft. Published assets remain unchanged.

The build starts from a pinned image, then installs packages from its distribution's
repositories. Package versions may change between builds; the process does
not promise identical output bytes.

The libvirt archive and catalogue are checked by CI. Runtime tests use the
QEMU provider on macOS. Linux libvirt runtime support needs its own runner
tests and an ARM64 UEFI setup before it can be claimed as tested.

Other operating systems, AMI builds and Docker images can be added when needed.
The MIT license covers the repository tools. Distribution packages keep their own licenses.

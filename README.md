# Image Factory

Image Factory builds ARM64 Vagrant boxes with Packer and tests them on
GitHub Actions. Each target owns its source, preparation scripts and guest
checks. Releases include the box, checksum, build manifest, test report and
Vagrant catalogue.

| Target | Guest | Usage guide |
| --- | --- | --- |
| `debian-13-arm64` (default) | Debian 13 | [Debian README](targets/debian-13-arm64/README.md) |
| `ubuntu-26.04-arm64` | Ubuntu Server 26.04 LTS | [Ubuntu README](targets/ubuntu-26.04-arm64/README.md) |

## Use a box on Apple Silicon

Install [Vagrant](https://developer.hashicorp.com/vagrant/install), QEMU and
the tested provider version:

```sh
brew install qemu
vagrant plugin install vagrant-qemu --plugin-version 0.6.3
```

Choose a target from the table above. Its README includes complete
`Vagrantfile` examples for one machine and two connected machines, with the
correct SSH account, native acceleration, resources and network options.

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
settings in the target guides are for physical Apple Silicon machines;
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
  <target>/
    README.md
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
image_factory/              # Target selection, archive checks and publication
scripts/                    # Build commands and shared zero-fill helper
tests/box/test_archive.py    # Common box and metadata tests
tests/box/linux/             # Linux guest tests
```

`target.json` defines the display name, architecture, box provider, communicator,
account, runtime suite and minimum test count. `guest.json` contains the guest
distribution, version, package and service expectations. The recipe owns its source updater,
build and validation commands, template and provisioning files. Shared commands
have no distribution name.

Each target README documents its source and consumer settings. Source
configuration stores the dated URL, build ID and checksum in
`source.pkrvars.json`. A new target provides its own preparation, cleanup and
runtime suite when the existing suite does not cover its guest behavior.

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

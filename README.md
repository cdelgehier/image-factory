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

## From an official image to a tested box

```mermaid
flowchart TD
    source["Official ARM64 image<br/>Pinned URL and checksum"]
    prepare["Packer and QEMU<br/>Verify source and prepare guest"]
    clean["Clean keys and identity<br/>Zero free space and shut down"]
    disk["Compressed QCOW2<br/>No backing file"]
    box["Vagrant box<br/>Disk, metadata and Vagrantfile"]
    tests["Archive and disk checks<br/>Two Vagrant clone tests"]
    ready["Box, checksum,<br/>manifest and test report"]
    source --> prepare --> clean --> disk --> box --> tests --> ready
```

Builds and guest tests use QEMU TCG on GitHub-hosted ARM64 runners.
The target usage guides use HVF for native acceleration on Apple Silicon.
The box uses the libvirt archive format accepted by `vagrant-qemu`.

## Add an image

Follow the [Rocky Linux tutorial](docs/tutorials/add-rocky-linux.md) to create
an ARM64 target from an official cloud image and submit it for GitHub build
and runtime tests. The tutorial includes the source updater, guest preparation
and tests needed by a Linux target.

## Build and test tasks

```sh
task ci
task sources:update
task box:build
task test:box
```

The tasks use the default target from `targets.json`. Pass `TARGET=<target-name>`
to select another enabled target.
`task ci` checks every enabled target. Build outputs are stored in
`dist/<target>/`; diagnostic logs are stored in `reports/`.

The [MIT license](LICENSE) covers the repository tools. Distribution packages
keep their own licenses.

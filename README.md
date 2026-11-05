# Image Factory

Image Factory builds ARM64 Vagrant boxes with Packer and tests them on
GitHub Actions. Each target owns its source, preparation scripts and guest
checks. Releases include the box, checksum, build manifest, test report and
Vagrant catalogue.

| Target | Guest | Usage guide |
| --- | --- | --- |
| `debian-13-arm64` (default) | Debian 13 | [Debian README](targets/debian-13-arm64/README.md) |
| `ubuntu-26.04-arm64` | Ubuntu Server 26.04 LTS | [Ubuntu README](targets/ubuntu-26.04-arm64/README.md) |
| `windows-11-arm64` (build yourself) | Windows 11 Pro, lean installation | [Windows README](targets/windows-11-arm64/README.md) |

Debian and Ubuntu boxes are published in releases. The Windows recipe requires
your Microsoft ISO and produces a private box. Its guide explains how to build,
test and reference the local file in your own Vagrant project. Windows is not
included in release assets, public catalogues or GitHub image build matrices.

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

GitHub Pages serves the stable catalogues; their box files stay in GitHub Releases.
This follows Vagrant's [custom box repository format](https://developer.hashicorp.com/vagrant/docs/boxes/box_repository).
Each catalogue lists all published versions with `arm64` entries for `libvirt` and `qemu`.
Both entries use the same box file and SHA-256 checksum. To pin a version, set
`config.vm.box_version` to a release version such as `0.4.0`.
Vagrant downloads the catalogue and the selected box during `vagrant up`.

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
    release["GitHub Releases<br/>Versioned box files"]
    catalogue["GitHub Pages<br/>Cumulative Vagrant catalogue"]
    use["vagrant up<br/>Download the selected version"]
    source --> prepare --> clean --> disk --> box --> tests --> ready
    ready --> release --> catalogue --> use
    release --> use
```

Published Linux builds and guest tests use QEMU TCG on GitHub-hosted ARM64 runners.
The target usage guides use HVF for native acceleration on Apple Silicon.
Windows builds use HVF on the user's Apple Silicon host.
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
to select another registered target.
`task ci` checks every registered recipe, including Windows configuration.
Build outputs are stored in
`dist/<target>/`; diagnostic logs are stored in `reports/`.

The [MIT license](LICENSE) covers the repository tools. Distribution packages
keep their own licenses.

## Browser catalogue and analytics

GitHub Pages publishes a small HTML landing page alongside the Vagrant JSON
catalogues. It lists the latest validated `.box` release assets for QEMU and libvirt.
The JSON files and their stable URLs stay unchanged.

The page uses self-hosted Umami at `https://stats.jackruby.qzz.io` with website
ID `b8aa4ed2-e140-4b1a-b748-0bcb62f2a657`. The tracker runs only on
`cdelgehier.github.io`, respects Do Not Track and excludes query strings and
fragments. Download clicks include the image name, version, architecture and
format. A click does not prove that the download finished; direct Vagrant,
`curl` and `wget` requests do not run the tracker.

Project links record `outbound-link` with `destination` set to `github`, `releases`
or `documentation`. Release-note links also include the version. GitHub Pages
serves a generated `404.html` for missing project pages. Its first page view becomes
a `page-not-found` event at `/image-factory/404`, without the unknown path or URL
parameters. Referrers keep only the source origin. These are browser events, so
missing API/JSON requests and command-line clients are not counted.

Deploy Umami from `cdelgehier/k3s-homelab` before publishing this change. Local
previews do not send analytics. Rebuild the Pages site with the existing
`Vagrant catalogue` workflow after deployment.

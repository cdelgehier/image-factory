# Windows 11 Pro ARM64

This recipe builds a private Windows box on Apple Silicon from your own
Microsoft installation ISO. **The Windows image is not included in GitHub
releases or the public Vagrant catalogue.** Build it, add the local `.box`
file to Vagrant, then reference that box in the project where you need it.

The recipe is prepared for Windows 11 Pro ARM64. Configuration and unit checks
run in CI. A complete Windows installation and the guest tests must pass on
your build host before you use the image. The recipe has not yet completed
an end-to-end Windows build.

## Prepare the source

You need macOS on Apple Silicon, at least 16 GiB of host RAM and 100 GiB of free
disk space. The guest uses four CPUs, 8 GiB of RAM and a 64 GiB virtual disk.
The QCOW2 file grows with written data; 64 GiB is not its download size.
QEMU detects zero writes during cleanup and frees those blocks on the host.
Keep space for the installed disk, its compressed copy, the box and test VMs.

Install [Vagrant](https://developer.hashicorp.com/vagrant/install) and the
build tools:

```sh
brew install qemu uv go-task shellcheck shfmt actionlint
brew tap hashicorp/tap
brew install hashicorp/tap/packer
brew install --cask powershell
vagrant plugin install vagrant-qemu --plugin-version 0.6.3
```

Download the official [Microsoft Windows 11 ARM64 ISO](https://www.microsoft.com/en-us/software-download/windows11arm64).
Choose English (United States). The current pin is build `26300.9457`:

```text
Windows11_Client_arm64_en-us_26300_9457.iso
SHA-256: 8992f2d2cfd2fa647e579e9eb31fc21f34da8333c29b6170fde17d2ed6623e9c
```

This checksum was calculated from Microsoft's complete en-US ISO download.
The setup files and Pro image metadata confirm English (United States), ARM64
and build `26300.9457`.

Microsoft's download links expire, so the recipe requires a file path instead
of saving a signed URL. It rejects an ISO whose checksum differs from
`source.pkrvars.json`. For newer media, download the ISO from Microsoft, confirm
its language, architecture and build, then calculate its SHA-256. Update the
source build and checksum together. Source updates are manual for this target.

The build downloads a pinned [UTM driver ISO](https://docs.getutm.app/guest-support/windows/)
and checks its SHA-256 before extracting the Windows 11 ARM64 network driver.
Storage uses Windows' own NVMe driver. No Windows disk from another project
is used.

Windows keeps its Microsoft license terms. The generic Pro setup key only
selects the edition; it does not activate Windows. You must have the rights
to install and use Windows. Starting this unattended build accepts the
Windows license terms in the answer file.

## Build and test

From the repository directory:

```sh
export WINDOWS_ISO="$HOME/Downloads/Windows11_Client_arm64_en-us_26300_9457.iso"
task ci
task box:build TARGET=windows-11-arm64
task test:box TARGET=windows-11-arm64
```

On Apple Silicon, `task ci` also checks UEFI startup with the real Packer and
QEMU tools. It uses a small temporary VM and empty media, without installing
Windows. This check does not replace the complete build and guest tests.

The build uses native HVF acceleration. The guest has no internet access during
installation, which avoids Store updates changing the image before Sysprep.
A `ramfb` display provides the physical UEFI framebuffer required by Windows
before a display driver is loaded. Keep it in the consumer configuration,
including for a headless VM.
A temporary password is generated for Packer and removed during cleanup.
Do not enable `PACKER_LOG`: debug logs can expose temporary credentials.

Build outputs:

- `dist/windows-11-arm64/windows-11-arm64.box`
- `dist/windows-11-arm64/windows-11-arm64.box.sha256`
- `dist/windows-11-arm64/disk/windows-11-arm64.qcow2`
- `dist/windows-11-arm64/windows-11-arm64-build-manifest.json`
- `dist/windows-11-arm64/windows-11-arm64-test-report.xml` after tests

The build refuses to replace an existing disk or box. Move outputs you want
to keep before rebuilding. Packer progress is shown in the terminal and saved
in `reports/windows-11-arm64-packer.log`. Ctrl+C asks Packer to cancel the build
and clean up its VM; wait for the command to finish. Each test run keeps its
own Vagrant logs and removes its test machines and imported box after success
or failure. Tests start two 8 GiB guests; 32 GiB of host RAM is recommended for
the full test run.

## What the image contains

The guest is a lean Windows 11 Pro installation. `lean.ps1` removes consumer
Appx packages from existing and future user profiles:

- Xbox and gaming apps, Solitaire, Clipchamp and personal Teams.
- Copilot, Family, Phone Link, Feedback Hub, Get Help and tips.
- News, Weather, Outlook, the Office promotion app, legacy media apps and People.

It also uninstalls OneDrive and enables CompactOS. The exact package names are
listed in the script. Windows Update, Defender, the Microsoft Store, Edge,
WebView2, the component store, Calculator and Paint are kept. This recipe does
not run Tiny11 Builder. It uses a smaller, explicit removal list and keeps the
system serviceable.

Cleanup removes temporary files and setup credentials, writes zero blocks to
free space, then runs Sysprep before shutdown. Packer compresses the standalone
QCOW2 disk and packages it in the libvirt box format accepted by `vagrant-qemu`.
There is no 2 GiB limit for this private box, and no promise of a final size.

The current provider has no built-in persistent TPM or Secure Boot setup.
The answer file bypasses these two Windows Setup checks and prevents automatic
device encryption. This is a lab VM configuration. UAC, Defender and the Windows
firewall remain enabled. WinRM uses HTTPS; each clone creates a new self-signed
certificate. Vagrant accepts this guest certificate and forwards WinRM only
to the host's loopback address.

## Add the local box to your project

After the build and tests pass, import the box. Run this command from the
repository directory:

```sh
vagrant box add \
  --name image-factory/windows-11-arm64 \
  --provider libvirt \
  --architecture arm64 \
  --checksum-type sha256 \
  --checksum "$(awk '{print $1}' dist/windows-11-arm64/windows-11-arm64.box.sha256)" \
  dist/windows-11-arm64/windows-11-arm64.box
```

Create a `Vagrantfile` in your own project:

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "image-factory/windows-11-arm64"
  config.vm.box_architecture = "arm64"
  config.vm.box_check_update = false

  config.vm.provider "qemu" do |qemu|
    qemu.arch = "aarch64"
    qemu.machine = "virt,accel=hvf"
    qemu.cpu = "host"
    qemu.smp = "4"
    qemu.memory = "8G"
    qemu.drive_interface = "none"
    qemu.net_device = "virtio-net-pci"
    qemu.extra_qemu_args = ["-device", "ramfb",
      "-device", "nvme,drive=disk0,serial=image-factory"]
  end

  config.vm.provision "shell", inline: "Write-Output 'Windows lab is ready'"
end
```

```sh
vagrant up --provider=qemu
vagrant winrm -c 'Get-ComputerInfo | Select-Object WindowsProductName, OsArchitecture'
vagrant halt
```

The bundled configuration sets the Windows guest type, HTTPS WinRM, port
forwarding and the `vagrant` account. Its initial lab password is `vagrant`.
Change it after the first boot, then set `IMAGE_FACTORY_WINDOWS_PASSWORD` for
later Vagrant commands. Changing the environment variable alone does not change
the guest password. Each fresh clone starts with the initial lab password.

Shared folders are disabled. Use Vagrant's `file` and PowerShell provisioners
to copy project files into the guest. The guest tests cover file upload, Windows
and ARM64 identity, administrator access, lean apps, retained servicing,
separate clone identities and certificates, shutdown and restart. Archive tests
check the metadata, disk checksum, QCOW2 integrity and lack of a backing file.

## References

[Bento's Windows 11 ARM64 answer file](https://github.com/chef/bento/tree/main/packer_templates/win_answer_files/11/arm64)
is a useful reference for the installation passes and ARM64 driver paths.
This recipe uses its own focused answer files and preparation scripts.
[Tiny11 Builder](https://github.com/ntdevlabs/tiny11builder) shows a broader
removal approach, with different tradeoffs for standard and Core builds.
[Microsoft's UEFI requirements](https://learn.microsoft.com/en-us/windows-hardware/drivers/bringup/uefi-requirements-that-apply-to-all-windows-platforms#protocol-requirements)
explain why Windows needs a physical framebuffer. The UEFI driver for a plain
VirtIO GPU supports only `PixelBltOnly`, which Windows does not accept.

# Ubuntu Server 26.04 LTS ARM64

[Image Factory](../../README.md) builds this box from the official Ubuntu
Server 26.04 LTS (Resolute Raccoon) ARM64 cloud image. It includes SSH,
passwordless sudo, Python 3, rsync, cloud-init and the QEMU guest agent.

| Setting | Value |
| --- | --- |
| Box | `cdelgehier/ubuntu-26.04-arm64` |
| SSH account | `ubuntu` |
| Architecture | `arm64` in Vagrant, `aarch64` in QEMU |
| Disk | 12 GiB, standalone QCOW2 |
| Catalogue | [ubuntu-26.04-arm64.json](https://github.com/cdelgehier/image-factory/releases/latest/download/ubuntu-26.04-arm64.json) |

The catalogue becomes available after a release is published. It contains
all published versions with `libvirt` and `qemu` entries for the same archive.
The tested provider is `vagrant-qemu` 0.6.3 on macOS.

## One machine on Apple Silicon

Follow the [installation steps](../../README.md#use-a-box-on-apple-silicon),
then create this `Vagrantfile` in an empty directory:

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "cdelgehier/ubuntu-26.04-arm64"
  config.vm.box_url = "https://github.com/cdelgehier/image-factory/releases/latest/download/ubuntu-26.04-arm64.json"
  config.vm.box_architecture = "arm64"
  config.vm.hostname = "ubuntu"
  config.vm.usable_port_range = 50_223..50_232
  config.ssh.username = "ubuntu"
  config.ssh.insert_key = true
  config.vm.synced_folder ".", "/vagrant", type: "rsync"

  config.vm.provider "qemu" do |qemu|
    qemu.arch = "aarch64"
    qemu.machine = "virt,accel=hvf"
    qemu.cpu = "host"
    qemu.smp = "2"
    qemu.memory = "2G"
    qemu.ssh_port = 50_222
    qemu.ssh_auto_correct = true
  end
end
```

HVF uses native acceleration on a physical Apple Silicon Mac. GitHub image
tests use TCG with CPU model `max`. The provider loads the ARM64 UEFI firmware
from the QEMU installation.

```sh
vagrant up --provider=qemu
vagrant ssh -c "sudo cloud-init status --wait"
vagrant ssh
vagrant rsync
vagrant halt
vagrant up --provider=qemu
vagrant destroy
```

Rsync copies files from the project directory to `/vagrant` in the guest.
Run `vagrant rsync` after changing host files; guest changes do not flow back.
Vagrant replaces the standard public key on first boot. Keep the `ubuntu`
account and `config.ssh.insert_key = true`.

To select a published version, add `config.vm.box_version = "0.2.0"` with
the version you need. The box supplies cloud-init user data and a boot timeout
of 2700 seconds. Wait for cloud-init before running guest setup commands.

## Two connected machines

Use this complete `Vagrantfile` instead of the single-machine example.
Each machine has internet access through QEMU user networking. A second
interface connects the guests through a loopback TCP socket on the Mac.
The private addresses are reachable from the guests; use `vagrant ssh` from
the host.

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "cdelgehier/ubuntu-26.04-arm64"
  config.vm.box_url = "https://github.com/cdelgehier/image-factory/releases/latest/download/ubuntu-26.04-arm64.json"
  config.vm.box_architecture = "arm64"
  config.ssh.username = "ubuntu"
  config.ssh.insert_key = true
  config.vm.synced_folder ".", "/vagrant", type: "rsync"

  [
    ["first", "192.168.76.11", "listen=127.0.0.1:50200", 50_222],
    ["second", "192.168.76.12", "connect=127.0.0.1:50200", 50_242]
  ].each do |name, address, socket_options, ssh_port|
    config.vm.define name do |machine|
      machine.vm.hostname = name
      machine.vm.network "private_network", ip: address
      machine.vm.usable_port_range = (ssh_port + 1)..(ssh_port + 10)

      machine.vm.provider "qemu" do |qemu|
        qemu.arch = "aarch64"
        qemu.machine = "virt,accel=hvf"
        qemu.cpu = "host"
        qemu.smp = "2"
        qemu.memory = "2G"
        qemu.ssh_port = ssh_port
        qemu.ssh_auto_correct = true
        qemu.advanced_network = true
        qemu.net_mode = :socket
        qemu.socket_opts = socket_options
      end
    end
  end
end
```

Start the listener before the connector:

```sh
vagrant up first --provider=qemu --no-parallel
vagrant ssh first -c "sudo cloud-init status --wait"
vagrant up second --provider=qemu --no-parallel
vagrant ssh second -c "sudo cloud-init status --wait"
vagrant ssh first -c "ping -c 3 192.168.76.12"
vagrant ssh second -c "ping -c 3 192.168.76.11"
vagrant rsync
vagrant destroy
```

Use the same startup order after stopping both machines. The socket connects
these two guests only. Separate labs need another socket port and separate
SSH ports and correction ranges. A `private_network` declaration also needs
`advanced_network` and a QEMU network backend to connect the guests.

## Source and checks

The recipe pins a dated Ubuntu 26.04 release image and its SHA-256 digest in
[source.pkrvars.json](source.pkrvars.json). The source updater reads Canonical's
released SimpleStreams metadata and checks the digest against the dated
`SHA256SUMS` file. It rejects daily images, another release or architecture,
unexpected URLs, invalid checksums and older build IDs.

GitHub workflows build this target and test the final archive, SSH account,
packages, cloud-init, rsync, restart, separate clone identities and socket
networking. See the [shared build and test tasks](../../README.md#build-and-test-tasks).

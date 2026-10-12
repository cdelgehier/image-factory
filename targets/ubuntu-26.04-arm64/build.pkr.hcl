packer {
  required_plugins {
    qemu = {
      version = "= 1.1.7"
      source  = "github.com/hashicorp/qemu"
    }
    vagrant = {
      version = "= 1.1.7"
      source  = "github.com/hashicorp/vagrant"
    }
  }
}

variable "source_url" { type = string }
variable "source_checksum" { type = string }
variable "source_build" { type = string }
variable "firmware" { type = string }
variable "output_path" {
  type    = string
  default = "dist/ubuntu-26.04-arm64/disk"
}
variable "ssh_key_path" {
  type    = string
  default = ".build/id_ed25519"
}
variable "build_key" {
  type    = string
  default = ""
}

locals {
  project = abspath("${path.root}/../..")
  recipe  = abspath(path.root)
}

source "qemu" "ubuntu" {
  iso_url              = var.source_url
  iso_checksum         = var.source_checksum
  disk_image           = true
  use_backing_file     = false
  format               = "qcow2"
  disk_size            = "12G"
  disk_interface       = "virtio"
  disk_compression     = true
  output_directory     = "${local.project}/${var.output_path}"
  vm_name              = "ubuntu-26.04-arm64.qcow2"
  qemu_binary          = "qemu-system-aarch64"
  accelerator          = "tcg"
  machine_type         = "virt"
  cpu_model            = "max"
  cpus                 = 2
  memory               = 2048
  headless             = true
  net_device           = "virtio-net-pci"
  efi_boot             = true
  efi_firmware_code    = var.firmware
  efi_firmware_vars    = "${dirname(var.firmware)}/edk2-arm-vars.fd"
  ssh_username         = "ubuntu"
  ssh_private_key_file = "${local.project}/${var.ssh_key_path}"
  ssh_timeout          = "45m"
  shutdown_command     = "sudo bash /tmp/image-factory-cleanup.sh"
  shutdown_timeout     = "10m"
  cd_label             = "cidata"
  cd_content = {
    "meta-data" = "instance-id: image-factory-${var.source_build}\nlocal-hostname: ubuntu\n"
    "user-data" = templatefile("${local.recipe}/user-data.pkrtpl", { build_key = var.build_key })
  }
  qemuargs = [
    ["-serial", "file:reports/build-serial.log"],
    ["-device", "virtio-serial-pci"],
    ["-chardev", "socket,path=.build/guest-agent.sock,server=on,wait=off,id=agent"],
    ["-device", "virtserialport,chardev=agent,name=org.qemu.guest_agent.0"],
  ]
}

build {
  sources = ["source.qemu.ubuntu"]

  provisioner "shell" {
    script          = "${local.recipe}/provision.sh"
    execute_command = "chmod +x {{ .Path }}; sudo env {{ .Vars }} {{ .Path }}"
  }
  provisioner "file" {
    source      = "${local.project}/packer/vagrant.pub"
    destination = "/tmp/image-factory-vagrant.pub"
  }
  provisioner "file" {
    source      = "${local.recipe}/cleanup.sh"
    destination = "/tmp/image-factory-cleanup.sh"
  }
  provisioner "file" {
    source      = "${local.project}/scripts/zero_free_space.py"
    destination = "/tmp/image-factory-zero-free-space.py"
  }
  post-processor "vagrant" {
    output               = "${local.project}/dist/ubuntu-26.04-arm64/ubuntu-26.04-arm64.box"
    architecture         = "arm64"
    compression_level    = 6
    vagrantfile_template = "${local.recipe}/Vagrantfile"
    keep_input_artifact  = true
  }
}

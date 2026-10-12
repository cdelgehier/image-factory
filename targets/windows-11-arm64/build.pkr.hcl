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
variable "iso_path" { type = string }
variable "seed_iso" { type = string }
variable "firmware" { type = string }
variable "firmware_vars" { type = string }
variable "build_password" {
  type      = string
  sensitive = true
}

locals {
  project = abspath("${path.root}/../..")
  recipe  = abspath(path.root)
}

source "qemu" "windows" {
  iso_url          = var.iso_path
  iso_checksum     = var.source_checksum
  disk_image       = false
  use_backing_file = false
  format           = "qcow2"
  disk_size        = "64G"
  disk_compression = true
  output_directory = "${local.project}/dist/windows-11-arm64/disk"
  vm_name          = "windows-11-arm64.qcow2"
  qemu_binary      = "qemu-system-aarch64"
  accelerator      = "hvf"
  machine_type     = "virt"
  cpu_model        = "host"
  cpus             = 4
  memory           = 8192
  headless         = true
  vga              = "none"
  communicator     = "winrm"
  winrm_port       = 5986
  winrm_username   = "vagrant"
  winrm_password   = var.build_password
  winrm_use_ssl    = true
  winrm_insecure   = true
  winrm_use_ntlm   = true
  winrm_timeout    = "2h"
  shutdown_command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:/ImageFactory/shutdown.ps1"
  shutdown_timeout = "30m"
  boot_wait        = "3s"
  boot_command     = [join("", [for attempt in range(20) : "<spacebar><wait1>"])]
  # Replace all drives and devices together: ARM Windows reads NVMe and USB media.
  # This avoids depending on an x86 storage driver during Windows Setup.
  qemuargs = [
    ["-netdev", "user,id=user.0,restrict=on,hostfwd=tcp:127.0.0.1:{{ .SSHHostPort }}-:5986"],
    ["-drive", "if=pflash,format=raw,readonly=on,file=${var.firmware}"],
    ["-drive", "if=pflash,format=raw,file=${var.firmware_vars}"],
    ["-drive", "if=none,id=disk0,format=qcow2,file={{ .OutputDir }}/{{ .Name }}"],
    ["-drive", "if=none,id=install,media=cdrom,readonly=on,file=${var.iso_path}"],
    ["-drive", "if=none,id=seed,media=cdrom,readonly=on,file=${var.seed_iso}"],
    ["-device", "nvme,drive=disk0,serial=image-factory"],
    ["-device", "qemu-xhci,id=xhci"],
    ["-device", "usb-storage,drive=install,bus=xhci.0"],
    ["-device", "usb-storage,drive=seed,bus=xhci.0"],
    ["-device", "usb-kbd,bus=xhci.0"],
    ["-device", "usb-tablet,bus=xhci.0"],
    ["-device", "virtio-gpu-pci"],
    ["-device", "virtio-net-pci,netdev=user.0"],
  ]
}

build {
  sources = ["source.qemu.windows"]
  provisioner "powershell" {
    script = "${local.recipe}/lean.ps1"
  }
  provisioner "file" {
    source      = "${local.recipe}/configure-winrm.ps1"
    destination = "C:/ImageFactory/configure-winrm.ps1"
  }
  provisioner "file" {
    source      = "${local.recipe}/cleanup.ps1"
    destination = "C:/ImageFactory/cleanup.ps1"
  }
  provisioner "file" {
    source      = "${local.recipe}/shutdown.ps1"
    destination = "C:/ImageFactory/shutdown.ps1"
  }
  provisioner "file" {
    source      = "${local.recipe}/Sysprep.xml"
    destination = "C:/ImageFactory/Sysprep.xml"
  }
  post-processor "vagrant" {
    output               = "${local.project}/dist/windows-11-arm64/windows-11-arm64.box"
    architecture         = "arm64"
    compression_level    = 6
    vagrantfile_template = "${local.recipe}/Vagrantfile"
    keep_input_artifact  = true
  }
}

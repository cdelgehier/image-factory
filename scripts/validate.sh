#!/usr/bin/env bash
set -euo pipefail

mkdir -p .build
ssh-keygen -q -t ed25519 -N '' -C image-factory-build -f .build/validate_ed25519
trap 'rm -f .build/validate_ed25519 .build/validate_ed25519.pub' EXIT
packer init packer/debian.pkr.hcl
packer fmt -check packer/debian.pkr.hcl
packer validate -var-file=sources/debian.pkrvars.json \
  -var 'output_path=.build/validation-disk' \
  -var 'ssh_key_path=.build/validate_ed25519' \
  -var "build_key=$(cat .build/validate_ed25519.pub)" \
  -var "firmware=/usr/share/qemu/edk2-aarch64-code.fd" packer/debian.pkr.hcl

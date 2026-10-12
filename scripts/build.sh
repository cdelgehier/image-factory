#!/usr/bin/env bash
set -euo pipefail

mkdir -p .build reports dist
if [[ -e dist/disk || -e dist/debian-13-arm64.box ]]; then
  echo "Build output already exists. Remove dist before starting another build." >&2
  exit 1
fi
ssh-keygen -q -t ed25519 -N '' -C image-factory-build -f .build/id_ed25519
trap 'rm -f .build/id_ed25519 .build/id_ed25519.pub .build/guest-agent.sock' EXIT
firmware="$(brew --prefix qemu)/share/qemu/edk2-aarch64-code.fd"
packer init packer/debian.pkr.hcl
packer build -color=false -on-error=cleanup -var-file=sources/debian.pkrvars.json \
  -var "build_key=$(cat .build/id_ed25519.pub)" \
  -var "firmware=$firmware" packer/debian.pkr.hcl 2>&1 | tee reports/packer.log
uv run python -m image_factory.artifacts

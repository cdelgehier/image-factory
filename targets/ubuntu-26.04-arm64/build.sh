#!/usr/bin/env bash
set -euo pipefail

target="$(uv run --locked python -m image_factory.targets field name)"
recipe="$(uv run --locked python -m image_factory.targets field directory)"
output="$(uv run --locked python -m image_factory.targets field dist)"
box="$(uv run --locked python -m image_factory.targets field box)"
export IMAGE_FACTORY_TARGET="$target"
mkdir -p .build reports "$output"
if [[ -e "$output/disk" || -e "$box" ]]; then
  echo "Build output already exists in $output. Remove it before starting another build." >&2
  exit 1
fi
ssh-keygen -q -t ed25519 -N '' -C image-factory-build -f .build/id_ed25519
trap 'rm -f .build/id_ed25519 .build/id_ed25519.pub .build/guest-agent.sock' EXIT
firmware="$(brew --prefix qemu)/share/qemu/edk2-aarch64-code.fd"
packer init "$recipe/build.pkr.hcl"
packer build -color=false -on-error=cleanup -var-file="$recipe/source.pkrvars.json" \
  -var "build_key=$(cat .build/id_ed25519.pub)" \
  -var "firmware=$firmware" "$recipe/build.pkr.hcl" 2>&1 | tee "reports/$target-packer.log"

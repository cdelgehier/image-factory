#!/usr/bin/env bash
set -euo pipefail

recipe="$(uv run --locked python -m image_factory.targets field directory)"
packer init "$recipe/build.pkr.hcl"
packer fmt -check "$recipe/build.pkr.hcl"
# Validation needs no Windows media or guest. Builds require the pinned local ISO.
# Use a separate destination so existing build outputs do not block checks.
packer validate -var-file="$recipe/source.pkrvars.json" \
  -var 'output_path=.build/windows-validation-disk' \
  -var 'iso_path=https://www.microsoft.com/windows.iso' \
  -var 'seed_iso=/tmp/image-factory-validation.iso' \
  -var 'firmware=/usr/share/qemu/edk2-aarch64-code.fd' \
  -var 'firmware_vars=/tmp/image-factory-validation.fd' \
  -var 'build_password=Validation-only-1234' "$recipe/build.pkr.hcl"

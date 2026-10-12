#!/usr/bin/env bash
set -euo pipefail

targets="$(uv run --locked python -m image_factory.targets list)"
while IFS= read -r target; do
  recipe="$(uv run --locked python -m image_factory.targets field directory --target "$target")"
  IMAGE_FACTORY_TARGET="$target" bash "$recipe/validate.sh"
done <<<"$targets"

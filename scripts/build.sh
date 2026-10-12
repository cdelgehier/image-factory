#!/usr/bin/env bash
set -euo pipefail

recipe="$(uv run --locked python -m image_factory.targets field directory)"
bash "$recipe/build.sh"
uv run --locked python -m image_factory.artifacts

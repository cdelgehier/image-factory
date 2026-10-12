#!/usr/bin/env bash
set -euo pipefail

recipe="$(uv run --locked python -m image_factory.targets field directory)"
# Run from the project root so the recipe can use the shared factory modules.
uv run --locked python -c 'import runpy, sys; runpy.run_path(sys.argv[1], run_name="__main__")' "$recipe/prepare.py"

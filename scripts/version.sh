#!/usr/bin/env bash
set -euo pipefail

set +e
uv run --locked cz bump --yes
status=$?
set -e
# Commitizen uses 21 when no commit needs a version change.
if [[ "$status" == 21 ]]; then
  exit 0
fi
if [[ "$status" != 0 ]]; then
  exit "$status"
fi
tag="$(uv run --locked python -c 'import tomllib; print("v" + tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
git push origin HEAD:main
git push origin "refs/tags/$tag"
task release:draft "TAG=$tag"

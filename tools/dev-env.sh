#!/usr/bin/env bash
# Create .venv with Exo at the pinned commit (exo.pin) and exo-os in editable mode.
# --system-site-packages so the surface can use the distribution's PyGObject and GTK4.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
pin="$(cat "$here/exo.pin")"
dep="$here/.deps/exo"
[[ -d $dep/.git ]] || git clone -q https://github.com/aska-digital/exo.git "$dep"
git -C "$dep" fetch -q origin
git -C "$dep" -c advice.detachedHead=false checkout -q "$pin"
[[ -d $here/.venv ]] || uv venv -q --system-site-packages "$here/.venv"
VIRTUAL_ENV="$here/.venv" uv pip install -q -e "$dep[tui,dev]" -e "$here[dev]"
echo "exo $(git -C "$dep" rev-parse --short HEAD) and exo-os installed in .venv"

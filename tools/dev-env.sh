#!/usr/bin/env bash
# Create .venv with Exo at the pinned commit (exo.pin) and exo-os in editable mode.
# --system-site-packages so the surface can use the distribution's PyGObject and GTK4.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
pin="$(cat "$here/exo.pin")"
dep="$here/.deps/exo"
[[ -d $dep/.git ]] || git clone -q https://github.com/aska-digital/exo.git "$dep"
git -C "$dep" fetch -q origin
if [[ -n $(git -C "$dep" status --porcelain) ]]; then
  echo "refusing: $dep has local modifications; stash or clean it before the dev env builds against the pin" >&2
  exit 1
fi
git -C "$dep" -c advice.detachedHead=false checkout -q "$pin"
if [[ ! -d $here/.venv ]]; then
  python="$(uv python find --system '>=3.11' 2>/dev/null || uv python find '>=3.11')"
  uv venv -q --python "$python" --system-site-packages "$here/.venv"
fi
VIRTUAL_ENV="$here/.venv" uv pip install -q -e "$dep[tui,dev]" -e "$here[dev]"
echo "exo $(git -C "$dep" rev-parse --short HEAD) and exo-os installed in .venv"

#!/usr/bin/env bash
# Build the Exo OS disk image with mkosi, unprivileged. Output: image/out/exo-os.raw
#   tools/build-image.sh [extra mkosi args]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
source "$here/tools/env.sh"
tools="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/exo-os"
mkdir -p "$cache/mkosi" "$here/image/out"
exec nice -n 10 "$tools/venv/bin/python3" -m mkosi -C "$here/image" \
  --extra-search-path="$tools/bin" \
  --extra-search-path="$tools/root/usr/bin" \
  --cache-directory="$cache/mkosi" \
  --output-directory="$here/image/out" \
  --force "$@" build

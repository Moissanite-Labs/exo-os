#!/usr/bin/env bash
# Build the Exo OS disk image with mkosi, unprivileged. Output: image/out/exo-os.raw
#   tools/build-image.sh [extra mkosi args]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
source "$here/tools/env.sh"
tools="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/exo-os"
mkdir -p "$cache/mkosi" "$here/image/out"

# Wheelhouse and plugins for the image: Exo at the pinned commit, exo-os from this tree, and their
# dependencies. The image's postinst installs from these offline.
pin="$(cat "$here/exo.pin")"
dep="$here/.deps/exo"
[[ -d $dep/.git ]] || git clone -q https://github.com/aska-digital/exo.git "$dep"
git -C "$dep" fetch -q origin
git -C "$dep" -c advice.detachedHead=false checkout -q "$pin"
extra="$here/image/out/extra"
rm -rf "$extra"
mkdir -p "$extra/opt/exo/wheels" "$extra/opt/exo/plugins"
uv build -q --wheel -o "$extra/opt/exo/wheels" "$dep"
uv build -q --wheel -o "$extra/opt/exo/wheels" "$here"
uvx -q pip download -q --only-binary=:all: --dest "$extra/opt/exo/wheels" \
  --python-version 3.14 --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
  "$extra"/opt/exo/wheels/exo-*.whl "textual>=1"
for p in exo-gate exo-memory exo-context; do cp -r "$dep/plugins/$p" "$extra/opt/exo/plugins/"; done
echo "$pin" > "$extra/opt/exo/EXO_PIN"
exec nice -n 10 "$tools/venv/bin/python3" -m mkosi -C "$here/image" \
  --extra-search-path="$tools/bin" \
  --extra-search-path="$tools/root/usr/bin" \
  --cache-directory="$cache/mkosi" \
  --output-directory="$here/image/out" \
  --extra-tree="$extra" \
  --force "$@" build

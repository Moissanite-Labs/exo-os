#!/usr/bin/env bash
# Build the Exo OS disk image with mkosi, unprivileged. Output: image/out/exo-os.raw
#   tools/build-image.sh [extra mkosi args]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
build_profile=$(python3 "$here/tools/build-profile.py" -- "$@")
source "$here/tools/env.sh"
tools="$EXO_OS_TOOLS_ACTIVE"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/exo-os"
mkdir -p "$cache/mkosi" "$here/image/out"

# Arch hosts use the isolated user-prefix toolchain from host-tools.sh. Other Linux build hosts can
# select a native mkosi executable and a mkosi tools tree (for example, Debian sid on an ARM host):
#   EXO_OS_MKOSI=/path/to/mkosi tools/build-image.sh --tools-tree=yes \
#     --tools-tree-distribution=debian --tools-tree-release=sid
if [[ -n ${EXO_OS_MKOSI:-} ]]; then
  mkosi_cmd=("$EXO_OS_MKOSI")
  mkosi_env=(env -u PYTHONPATH)
  extra_search_args=()
else
  mkosi_cmd=("$tools/venv/bin/python3" -m mkosi)
  mkosi_env=(env)
  extra_search_args=("--extra-search-path=$tools/bin" "--extra-search-path=$tools/root/usr/bin")
fi

# Wheelhouse and plugins for the image: Exo at the pinned commit, exo-os from this tree, and their
# dependencies. The image's postinst installs from these offline.
pin="$(cat "$here/exo.pin")"
dep="$here/.deps/exo"
if [[ -d $dep/.git ]]; then
  git -C "$dep" fetch -q origin
  git -C "$dep" -c advice.detachedHead=false checkout -q "$pin"
elif [[ -f $dep/.EXO_PIN ]]; then
  [[ $(tr -d '\n' < "$dep/.EXO_PIN") == "$pin" ]] || {
    echo "core source archive pin does not match exo.pin ($pin)" >&2
    exit 1
  }
else
  git clone -q https://github.com/aska-digital/exo.git "$dep"
  git -C "$dep" fetch -q origin
  git -C "$dep" -c advice.detachedHead=false checkout -q "$pin"
fi
extra="$here/image/out/extra"
rm -rf "$extra"
mkdir -p "$extra/opt/exo/wheels" "$extra/opt/exo/plugins"
uv build -q --wheel -o "$extra/opt/exo/wheels" "$dep"
uv build -q --wheel -o "$extra/opt/exo/wheels" "$here"
uvx -q pip download -q --only-binary=:all: --dest "$extra/opt/exo/wheels" \
  --python-version 3.14 --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
  "$extra"/opt/exo/wheels/exo-*.whl "textual>=1"
# Runtime plugin folders: required executor mode does not load ambient entry points.
for p in exo-gate exo-memory exo-context; do cp -r "$dep/plugins/$p" "$extra/opt/exo/plugins/"; done
# Include the OS adapter in the generated extra tree as well as the checked-in
# overlay so either extra-tree application order has the complete inventory.
cp -r "$here/image/mkosi.extra/opt/exo/plugins/exo-os" "$extra/opt/exo/plugins/"
# End runtime plugin folders.
echo "$pin" > "$extra/opt/exo/EXO_PIN"
python3 "$here/tools/build-provenance.py" \
  --source-root "$here" --pin "$pin" --profile "$build_profile" \
  --output "$extra/opt/exo/EXO_OS_BUILD.json"
exec nice -n 10 "${mkosi_env[@]}" "${mkosi_cmd[@]}" -C "$here/image" \
  "${extra_search_args[@]}" \
  --cache-directory="$cache/mkosi" \
  --output-directory="$here/image/out" \
  --extra-tree="$extra" \
  --force "$@" build

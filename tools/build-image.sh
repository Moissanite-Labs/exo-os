#!/usr/bin/env bash
# Build the Exo OS disk image with mkosi, unprivileged. Output: image/out/exo-os.raw
#   tools/build-image.sh [--profile=vmtest] [documented --tools-tree options]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd -P)"
build_profile=$(python3 "$here/tools/build-profile.py" -- "$@")
for local_config in "$here/image/mkosi.local.conf" "$here/image/mkosi.local"; do
  if [[ -e $local_config || -L $local_config ]]; then
    echo "local mkosi configuration is unsupported: $local_config" >&2
    exit 2
  fi
done
python3 "$here/tools/source_identity.py" --source-root "$here" \
  --require-clean --label 'build source' >/dev/null
source "$here/tools/env.sh"
tools="$EXO_OS_TOOLS_ACTIVE"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/exo-os"
mkdir -p "$cache/mkosi" "$here/image/out"
out="$here/image/out"
# The lock also excludes acceptance, which keeps the published pair stable.
mkdir "$out/.build-lock" 2>/dev/null || {
  echo 'another build or acceptance run holds image/out/.build-lock' >&2
  exit 2
}
build_cleanup() {
  local rc=$?
  trap - EXIT
  rmdir "$out/.build-lock" 2>/dev/null || true
  exit "$rc"
}
trap build_cleanup EXIT
# Retain failed attempts and prior successful artifacts for diagnosis. A new
# admitted attempt invalidates success before any payload staging or mkosi call.
stage=$(mktemp -d "$out/.build-attempt.XXXXXXXX")
if [[ -e $out/exo-os-build-success.json || -L $out/exo-os-build-success.json ]]; then
  mv "$out/exo-os-build-success.json" "$stage/prior-success.json"
fi

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
[[ $pin =~ ^[0-9a-f]{40}$ ]] || {
  echo 'exo.pin must contain a full lowercase 40-hex commit' >&2
  exit 2
}
mkdir -p "$here/.deps"
[[ $(cd "$here/.deps" && pwd -P) == "$here/.deps" ]] || {
  echo 'core dependency directory must be inside this source tree' >&2
  exit 2
}
if [[ -e $dep || -L $dep ]]; then
  [[ -d $dep && $(cd "$dep" && pwd -P) == "$dep" ]] || {
    echo 'core source directory must be inside this source tree' >&2
    exit 2
  }
fi
require_core_checkout() {
  local top changes
  [[ $(cd "$dep" && pwd -P) == "$dep" ]] || {
    echo 'core checkout must be inside this source tree' >&2
    exit 2
  }
  top=$(git -C "$dep" rev-parse --show-toplevel)
  [[ $(cd "$top" && pwd -P) == "$dep" ]] || {
    echo 'core source must be its own Git checkout' >&2
    exit 2
  }
  changes=$(git -C "$dep" status --porcelain --untracked-files=all) || {
    echo 'core checkout status could not be verified' >&2
    exit 2
  }
  [[ -z $changes ]] || {
    echo 'core source must be a clean committed checkout' >&2
    exit 2
  }
}
core_git=0
if [[ -e $dep/.git || -L $dep/.git ]]; then
  # A .git file is legitimate for an isolated worktree. Validate the physical
  # source root and refuse local changes before fetch/checkout can mutate it.
  require_core_checkout
  git -C "$dep" fetch -q origin
  git -C "$dep" -c advice.detachedHead=false checkout -q --detach "$pin"
  core_git=1
elif [[ -f $dep/.EXO_PIN ]]; then
  [[ $(tr -d '\n' < "$dep/.EXO_PIN") == "$pin" ]] || {
    echo "core source archive pin does not match exo.pin ($pin)" >&2
    exit 1
  }
else
  git clone -q https://github.com/aska-digital/exo.git "$dep"
  require_core_checkout
  git -C "$dep" fetch -q origin
  git -C "$dep" -c advice.detachedHead=false checkout -q --detach "$pin"
  core_git=1
fi
if [[ $core_git == 1 ]]; then
  require_core_checkout
  [[ $(git -C "$dep" rev-parse HEAD) == "$pin" ]] || {
    echo 'core checkout HEAD does not match exo.pin' >&2
    exit 2
  }
fi
extra="$stage/extra"
mkdir -p "$extra/opt/exo/wheels" "$extra/opt/exo/plugins"
# The guest runs whatever `python` Arch rolling ships, but the offline wheelhouse is resolved for one
# interpreter version. Record it in the image; mkosi.postinst.chroot refuses to build if the guest
# differs, rather than failing later in pip. When Arch moves on, set EXO_OS_GUEST_PYTHON (or change
# this default) and rebuild.
guest_python="${EXO_OS_GUEST_PYTHON:-3.14}"
[[ $guest_python =~ ^3\.[0-9]+$ ]] || {
  echo "EXO_OS_GUEST_PYTHON must look like 3.14, got '$guest_python'" >&2
  exit 2
}
echo "$guest_python" > "$extra/opt/exo/GUEST_PYTHON"
uv build -q --wheel -o "$extra/opt/exo/wheels" "$dep"
uv build -q --wheel -o "$extra/opt/exo/wheels" "$here"
uvx -q pip download -q --only-binary=:all: --dest "$extra/opt/exo/wheels" \
  --python-version "$guest_python" --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
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
nice -n 10 "${mkosi_env[@]}" "${mkosi_cmd[@]}" -C "$here/image" \
  ${extra_search_args[@]+"${extra_search_args[@]}"} \
  --cache-directory="$cache/mkosi" \
  --output-directory="$stage" \
  --extra-tree="$extra" \
  --force "$@" build

# set -e guarantees publication is reached only after mkosi exits successfully.
python3 "$here/tools/build-receipt.py" publish --stage "$stage" --out "$out"

#!/usr/bin/env bash
# Fetch the image build tools (mkosi, QEMU, OVMF and their missing libraries) into a user prefix,
# without root and without touching the host's package database. Arch hosts only.
#
#   tools/host-tools.sh            # install into ~/.local/opt/exo-os-tools
#   source tools/env.sh            # put them on PATH for this shell
set -euo pipefail

PREFIX="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}"
PKGS=(mkosi systemd-ukify python-pefile sbsigntools qemu-hw-display-virtio-gpu qemu-hw-display-virtio-gpu-pci qemu-hw-display-virtio-gpu-gl qemu-hw-display-virtio-gpu-pci-gl qemu-ui-gtk qemu-ui-opengl qemu-ui-egl-headless virglrenderer qemu-hw-display-virtio-vga-gl qemu-hw-display-virtio-vga qemu-system-x86 qemu-img edk2-ovmf mtools erofs-utils squashfs-tools arch-install-scripts)

command -v pacman >/dev/null || { echo "needs an Arch host (pacman)" >&2; exit 1; }
mkdir -p "$PREFIX/cache" "$PREFIX/root"

# -Sp prints the URLs pacman would download for what is missing on this host; it needs no root.
mapfile -t urls < <(pacman -Sp --needed "${PKGS[@]}")
for url in "${urls[@]}"; do
  file="$PREFIX/cache/${url##*/}"
  [[ -s $file ]] || curl -fsSL --retry 3 -o "$file" "$url"
  bsdtar -xf "$file" -C "$PREFIX/root" --exclude '.PKGINFO' --exclude '.BUILDINFO' \
    --exclude '.MTREE' --exclude '.INSTALL'
done
# mkosi runs ukify inside its own sandbox, where PYTHONPATH is not passed through. Python always puts a
# script's own folder first on sys.path, so put ukify and the modules it imports side by side.
site="$(echo "$PREFIX"/root/usr/lib/python3*/site-packages)"
mkdir -p "$PREFIX/bin"
cp "$PREFIX/root/usr/bin/ukify" "$PREFIX/bin/ukify"
cp -r "$site/pefile.py" "$site/peutils.py" "$site/ordlookup" "$PREFIX/bin/"
# mkosi also runs `python3 -c "import pefile"` in that sandbox. When mkosi itself runs from an interpreter
# outside /usr it looks python3 up on its search path, so give it a venv whose .pth adds our modules.
[[ -x $PREFIX/venv/bin/python3 ]] || python3 -m venv --without-pip "$PREFIX/venv"
echo "$site" > "$(echo "$PREFIX"/venv/lib/python3*/site-packages)/exo-os-tools.pth"
rm -f "$PREFIX/bin/python3"
printf '#!/bin/sh\nexec "%s/venv/bin/python3" "$@"\n' "$PREFIX" > "$PREFIX/bin/python3"
chmod +x "$PREFIX/bin/python3"
echo "installed ${#urls[@]} packages into $PREFIX/root"

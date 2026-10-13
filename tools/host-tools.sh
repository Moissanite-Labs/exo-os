#!/usr/bin/env bash
# Fetch the image build tools (mkosi, QEMU, OVMF and their missing libraries) into a user prefix,
# without root and without touching the host's package database. Arch hosts only.
#
# pacman is only asked which URLs it would download. Every package is fetched over https together with its
# detached signature and checked against the Arch keyring before anything is unpacked; any failure stops the
# script. This checks the signing key against the keyring and its revocation list, not pacman's full web of trust.
#
#   tools/host-tools.sh            # install into ~/.local/opt/exo-os-tools
#   source tools/env.sh            # put them on PATH for this shell
set -euo pipefail

PREFIX="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}"
KEYRINGS="${EXO_OS_KEYRINGS:-/usr/share/pacman/keyrings}"
PKGS=(mkosi systemd-ukify python-pefile sbsigntools qemu-hw-display-virtio-gpu qemu-hw-display-virtio-gpu-pci qemu-hw-display-virtio-gpu-gl qemu-hw-display-virtio-gpu-pci-gl qemu-ui-gtk qemu-ui-opengl qemu-ui-egl-headless virglrenderer qemu-hw-display-virtio-vga-gl qemu-hw-display-virtio-vga qemu-system-x86 qemu-img edk2-ovmf mtools erofs-utils squashfs-tools arch-install-scripts)

die() { echo "host-tools: $*" >&2; exit 1; }

command -v pacman >/dev/null || { echo "needs an Arch host (pacman)" >&2; exit 1; }
for tool in curl bsdtar gpgv python3; do
  command -v "$tool" >/dev/null || die "needs $tool"
done
keyring="$KEYRINGS/archlinux.gpg"
revoked="$KEYRINGS/archlinux-revoked"
[[ -s $keyring && -s $revoked ]] || die "the Arch keyring is missing from $KEYRINGS (install archlinux-keyring)"

gnupghome="$(mktemp -d)"
trap 'rm -rf "$gnupghome"' EXIT
chmod 700 "$gnupghome"

# verify FILE SIG: true only for a valid signature by a key in the Arch keyring that is not revoked.
verify() {
  local status fpr
  status="$(gpgv --homedir "$gnupghome" --keyring "$keyring" --status-fd 1 "$2" "$1" 2>/dev/null)" || return 1
  # VALIDSIG <signing key fingerprint> ... <primary key fingerprint>
  while read -r fpr; do
    [[ $fpr =~ ^[0-9A-Fa-f]{40}$ ]] || return 1
    ! awk -v f="$fpr" 'toupper($1) == toupper(f) { found = 1 } END { exit !found }' "$revoked" || return 1
  done < <(awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" { print $3; print $NF }' <<<"$status" | sort -u)
  grep -q '^\[GNUPG:\] VALIDSIG ' <<<"$status"
}

# fetch URL DEST: download to DEST.part and move it into place only when curl succeeds.
fetch() {
  rm -f "$2.part"
  curl -fsSL --retry 3 --proto '=https' --proto-redir '=https' -o "$2.part" "$1" || { rm -f "$2.part"; return 1; }
}

mkdir -p "$PREFIX/cache"

# -Sp prints the URLs pacman would download for what is missing on this host; it needs no root.
listing="$(pacman -Sp --needed "${PKGS[@]}")"
urls=(); while IFS= read -r u; do [[ -z $u ]] || urls+=("$u"); done <<<"$listing"
if ((${#urls[@]} == 0)); then
  echo "nothing to fetch: this host already has every package"
  exit 0
fi

# Check every URL before fetching any.
for url in "${urls[@]}"; do
  [[ $url =~ ^https://[^/?#[:space:]]+/[^?#[:space:]]*/[A-Za-z0-9._+@%:~-]+\.pkg\.tar\.(zst|xz)$ ]] \
    || die "refusing $url (packages must come from an https mirror; put one first in the mirrorlist)"
done

files=()
for url in "${urls[@]}"; do
  file="$PREFIX/cache/${url##*/}"
  # A cached file is trusted only if it verifies again; older runs cached files that were never checked.
  if ! { [[ -s $file && -s $file.sig ]] && verify "$file" "$file.sig"; }; then
    rm -f "$file" "$file.sig"
    fetch "$url" "$file" || die "download failed for $url"
    fetch "$url.sig" "$file.sig" || { rm -f "$file.part"; die "download of the signature failed for $url"; }
    verify "$file.part" "$file.sig.part" || { rm -f "$file.part" "$file.sig.part"; die "signature check failed for $url"; }
    mv "$file.part" "$file"
    mv "$file.sig.part" "$file.sig"
  fi
  files+=("$file")
done

# Unpack into a fresh directory and swap it in, so files from an older Python or an old package set cannot
# linger and a failed run leaves the previous install alone.
stage="$PREFIX/root.new"
rm -rf "$stage"
mkdir -p "$stage"
for file in "${files[@]}"; do
  bsdtar -xf "$file" -C "$stage" --exclude '.PKGINFO' --exclude '.BUILDINFO' \
    --exclude '.MTREE' --exclude '.INSTALL'
done
rm -rf "$PREFIX/root.old"
if [[ -e $PREFIX/root ]]; then mv "$PREFIX/root" "$PREFIX/root.old"; fi
mv "$stage" "$PREFIX/root"
rm -rf "$PREFIX/root.old"

# mkosi runs ukify inside its own sandbox, where PYTHONPATH is not passed through. Python always puts a
# script's own folder first on sys.path, so put ukify and the modules it imports side by side.
# Take the one site-packages directory that holds pefile: the host's Python minor version if present,
# otherwise the highest.
host_python="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
site="" best=""
while IFS= read -r dir; do
  [[ -f $dir/pefile.py ]] || continue
  [[ -n $best ]] || best=$dir
  if [[ ${dir%/site-packages} == */python$host_python ]]; then site=$dir; break; fi
done < <(printf '%s\n' "$PREFIX"/root/usr/lib/python3*/site-packages | sort -rV)
site="${site:-$best}"
[[ -n $site ]] || die "python-pefile was not among the packages fetched"
[[ -f $PREFIX/root/usr/bin/ukify ]] || die "systemd-ukify was not among the packages fetched"
mkdir -p "$PREFIX/bin"
cp "$PREFIX/root/usr/bin/ukify" "$PREFIX/bin/ukify"
rm -rf "$PREFIX/bin/ordlookup"
cp -r "$site/pefile.py" "$site/peutils.py" "$site/ordlookup" "$PREFIX/bin/"
# mkosi also runs `python3 -c "import pefile"` in that sandbox. When mkosi itself runs from an interpreter
# outside /usr it looks python3 up on its search path, so give it a venv whose .pth adds our modules.
# The venv is rebuilt each run: its site-packages directory is named after the Python that made it.
rm -rf "$PREFIX/venv"
python3 -m venv --without-pip "$PREFIX/venv"
venv_site="$("$PREFIX/venv/bin/python3" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
echo "$site" > "$venv_site/exo-os-tools.pth"
rm -f "$PREFIX/bin/python3"
printf '#!/bin/sh\nexec "%s/venv/bin/python3" "$@"\n' "$PREFIX" > "$PREFIX/bin/python3"
chmod +x "$PREFIX/bin/python3"
echo "installed ${#urls[@]} packages into $PREFIX/root"

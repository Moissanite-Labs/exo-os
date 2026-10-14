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
# The Python supervisor holds an advisory lock for this entire process, including publication.
if [[ ${EXO_OS_HOST_TOOLS_LOCKED:-} != "$PREFIX" ]]; then
  exec python3 "$(dirname "$0")/host-tools-generation.py" run "$PREFIX" "$0"
fi
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

# -Sp resolves the entire requested inventory, even packages already installed on the host. An empty
# cache directory of ours stands in for the host's, so a package cached there still gets a remote URL
# (otherwise pacman prints file:///var/cache/... for it) and is fetched and verified like the rest.
mkdir "$gnupghome/pacman-cache"
listing="$(pacman -Sp --cachedir "$gnupghome/pacman-cache" "${PKGS[@]}")"
urls=(); while IFS= read -r u; do [[ -z $u ]] || urls+=("$u"); done <<<"$listing"
if ((${#urls[@]} == 0)); then
  die "pacman returned no packages for the requested inventory"
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

# A generation has its final absolute pathname from creation onward: venv shebangs and .pth
# entries must never be invalidated by renaming the directory after construction.
generation="$(mktemp -d "$PREFIX/generations/gen-XXXXXXXX")"
stage="$generation/root"
mkdir -p "$stage" "$generation/bin"
for file in "${files[@]}"; do
  bsdtar -xf "$file" -C "$stage" --exclude '.PKGINFO' --exclude '.BUILDINFO' \
    --exclude '.MTREE' --exclude '.INSTALL'
done

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
done < <(printf '%s\n' "$generation"/root/usr/lib/python3*/site-packages | sort -rV)
site="${site:-$best}"
[[ -n $site ]] || die "python-pefile was not among the packages fetched"
[[ -f $generation/root/usr/bin/ukify ]] || die "systemd-ukify was not among the packages fetched"
mkdir -p "$generation/bin"
cp "$generation/root/usr/bin/ukify" "$generation/bin/ukify"
cp -r "$site/pefile.py" "$site/peutils.py" "$site/ordlookup" "$generation/bin/"
# mkosi also runs `python3 -c "import pefile"` in that sandbox. When mkosi itself runs from an interpreter
# outside /usr it looks python3 up on its search path, so give it a venv whose .pth adds our modules.
# The venv is rebuilt each run: its site-packages directory is named after the Python that made it.

python3 -m venv --without-pip "$generation/venv"
venv_site="$("$generation/venv/bin/python3" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
echo "$site" > "$venv_site/exo-os-tools.pth"
rm -f "$generation/bin/python3"
printf '#!/bin/sh\nexec "%s/venv/bin/python3" "$@"\n' "$generation" > "$generation/bin/python3"
chmod +x "$generation/bin/python3"
# Validate all requested command entrypoints and Python modules before changing the live pointer.
for entry in mkosi ukify sbsign qemu-system-x86_64 qemu-img mcopy mkfs.erofs mksquashfs pacstrap; do
  [[ -x $stage/usr/bin/$entry ]] || die "missing required tool: $entry"
done
for firmware in OVMF_CODE.4m.fd OVMF_VARS.4m.fd; do
  [[ -s $stage/usr/share/edk2/x64/$firmware ]] || die "missing firmware: $firmware"
done
"$generation/venv/bin/python3" -I -c 'import pefile, peutils, ordlookup, mkosi'
"$generation/bin/python3" -I -c 'import pefile, peutils, ordlookup, mkosi'
printf 'validated\n' > "$generation/.complete"
python3 "$(dirname "$0")/host-tools-generation.py" publish "$PREFIX" "$generation"
echo "installed ${#urls[@]} packages into $generation/root; published through $PREFIX/current"

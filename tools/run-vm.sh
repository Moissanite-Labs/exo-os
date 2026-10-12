#!/usr/bin/env bash
# Boot the Exo OS image in QEMU with KVM. The base image is never written: writes go to a throwaway
# overlay unless --keep is given.
#
#   tools/run-vm.sh [--gui] [--keep] [--mem MB]
#
# With the vmtest profile, SSH is on 127.0.0.1:$EXO_OS_SSH_PORT (default 2222) as root, with the key
# in image/out/vmtest-key. The serial console goes to image/out/console.log.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
source "$here/tools/env.sh"
out="$here/image/out"
fw="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root/usr/share/edk2/x64"
gui=0 keep=0 mem=2048
while (($#)); do
  case $1 in
    --gui) gui=1 ;; --keep) keep=1 ;; --mem) mem=$2; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

[[ -f $out/vmtest-key ]] || ssh-keygen -q -t ed25519 -N '' -C exo-os-vmtest -f "$out/vmtest-key"
cp "$fw/OVMF_VARS.4m.fd" "$out/vars.fd"
disk="$out/exo-os.raw"
if ((!keep)); then
  rm -f "$out/overlay.qcow2"
  qemu-img create -q -f qcow2 -F raw -b "$disk" "$out/overlay.qcow2"
  disk="$out/overlay.qcow2"
fi

display=(-display none -vga none -device virtio-gpu-pci)
((gui)) && display=(-display gtk -device virtio-gpu-pci)

exec qemu-system-x86_64 \
  -machine q35,accel=kvm -cpu host -smp 2 -m "$mem" \
  -drive if=pflash,format=raw,readonly=on,file="$fw/OVMF_CODE.4m.fd" \
  -drive if=pflash,format=raw,file="$out/vars.fd" \
  -drive if=virtio,file="$disk",format="$([[ $disk == *.qcow2 ]] && echo qcow2 || echo raw)" \
  -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${EXO_OS_SSH_PORT:-2222}-:22 \
  -smbios type=11,value=io.systemd.credential:ssh.authorized_keys.root="$(cat "$out/vmtest-key.pub")" \
  -serial file:"$out/console.log" \
  -monitor unix:"$out/monitor.sock",server,nowait \
  "${display[@]}"

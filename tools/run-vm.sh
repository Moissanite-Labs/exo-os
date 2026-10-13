#!/usr/bin/env bash
# Boot the Exo OS image in QEMU. The base image is never written: writes go to a throwaway overlay
# unless --keep is given. KVM is the default; set EXO_OS_VM_ACCEL=tcg and EXO_OS_VM_CPU=max when
# host acceleration is unavailable. EXO_OS_VM_SMP controls the vCPU count.
#
#   tools/run-vm.sh [--gui] [--gl] [--keep] [--mem MB]
#
# --gl renders the guest's desktop with the host GPU (virgl, EGL headless, no window). Hyprland needs
# OpenGL; without --gl the guest falls back to software rendering if it can.
#
# With the vmtest profile, SSH is on 127.0.0.1:$EXO_OS_SSH_PORT (default 2222) as root, with the key
# in image/out/vmtest-key. The serial console goes to image/out/console.log.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
source "$here/tools/env.sh"
out="$here/image/out"
mkdir -p "$out"
fw_dir="${EXO_OS_TOOLS:-$HOME/.local/opt/exo-os-tools}/root/usr/share/edk2/x64"
fw="${EXO_OS_VM_FIRMWARE_CODE:-${EXO_OS_FIRMWARE:-$fw_dir/OVMF_CODE.4m.fd}}"
fw_vars="${EXO_OS_VM_FIRMWARE_VARS:-${EXO_OS_FIRMWARE_VARS:-$fw_dir/OVMF_VARS.4m.fd}}"
accel="${EXO_OS_VM_ACCEL:-kvm}"
cpu="${EXO_OS_VM_CPU:-host}"
smp="${EXO_OS_VM_SMP:-2}"
gui=0 gl=0 keep=0 mem=2048
while (($#)); do
  case $1 in
    --gui) gui=1 ;; --gl) gl=1 ;; --keep) keep=1 ;; --mem)
      [[ ${2:-} =~ ^[1-9][0-9]*$ ]] || { echo "--mem needs a number of megabytes" >&2; exit 2; }
      mem=$2; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

[[ -f $out/vmtest-key ]] || ssh-keygen -q -t ed25519 -N '' -C exo-os-vmtest -f "$out/vmtest-key"
cp "$fw_vars" "$out/vars.fd"
disk="$out/exo-os.raw"
if ((!keep)); then
  rm -f "$out/overlay.qcow2"
  qemu-img create -q -f qcow2 -F raw -b "$disk" "$out/overlay.qcow2"
  disk="$out/overlay.qcow2"
fi

display=(-display none -vga none -device virtio-gpu-pci)
((gl)) && display=(-display egl-headless,rendernode=/dev/dri/renderD128 -vga none -device virtio-vga-gl)
((gui)) && display=(-display gtk,gl=$( ((gl)) && echo on || echo off) -device "$( ((gl)) && echo virtio-vga-gl || echo virtio-gpu-pci)")

exec qemu-system-x86_64 \
  -uuid "${EXO_OS_VM_UUID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}" \
  -machine "q35,accel=$accel" -cpu "$cpu" -smp "$smp" -m "$mem" \
  -drive if=pflash,format=raw,readonly=on,file="$fw" \
  -drive if=pflash,format=raw,file="$out/vars.fd" \
  -drive if=virtio,file="$disk",format="$([[ $disk == *.qcow2 ]] && echo qcow2 || echo raw)" \
  -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:${EXO_OS_SSH_PORT:-2222}-:22 \
  -smbios type=11,value=io.systemd.credential:ssh.authorized_keys.root="$(cat "$out/vmtest-key.pub")" \
  -serial file:"$out/console.log" \
  -monitor unix:"$out/monitor.sock",server,nowait \
  -qmp unix:"$out/qmp.sock",server,nowait \
  "${display[@]}"

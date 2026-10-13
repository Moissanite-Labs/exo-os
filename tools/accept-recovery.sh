#!/usr/bin/env bash
# Actual reboot acceptance, only in the harness-identified disposable vmtest VM.
# This script never mounts/modifies the host image, home, store or bootloader.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-recovery.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
source "$here/tools/accept-common.sh"
accept_start recovery 2048
required_checks=5
accept_keyring
old_boot=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id')
ssh_vm 'test ! -e /etc/exo-recovery-proof; printf old-system > /etc/exo-recovery-proof'
checkpoint=$(ssh_vm 'exo-os-recovery checkpoint')
id=$(echo "$checkpoint" | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["id"])')
[[ $id =~ ^[0-9a-f]{32}$ ]]
# Newer data must survive selection of an older system snapshot.
ssh_vm 'printf newer-home > /home/exo/exo-recovery-proof; printf newer-store > /var/lib/exo/exo-recovery-proof; printf broken-system > /etc/exo-recovery-proof'
ssh_vm 'as-exo systemctl --user stop exod'
store_before=$(ssh_vm 'find /var/lib/exo/exo -type f -exec sha256sum {} + | LC_ALL=C sort | sha256sum')
staged=$(ssh_vm "exo-os-recovery stage $id")
entry=$(echo "$staged" | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["entry"])')
[[ $entry == "exo-recovery-$id.conf" ]]
# Prove the bootloader actually recognizes the staged entry, then select once.
ssh_vm "bootctl list --json=short" | "$here/.venv/bin/python" -c 'import json,sys; wanted=sys.argv[1]; rows=json.load(sys.stdin); assert any(r.get("id") == wanted for r in rows)' "$entry"
result menu 1 "systemd-boot recognizes $entry"
ssh_vm "bootctl set-oneshot $entry"
reboot_rc=0
ssh_vm 'systemctl reboot' || reboot_rc=$?
[[ $reboot_rc == 0 || $reboot_rc == 255 ]]
ready=0
for attempt in $(seq 1 90); do
  kill -0 "$qemu" 2>/dev/null || { result reboot 0 'QEMU exited during recovery reboot'; exit 1; }
  current=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null) || current=''
  if [[ -n $current && $current != "$old_boot" ]]; then ready=1; break; fi
  sleep 3
done
[[ $ready == 1 ]] || { result reboot 0 'Recovery did not become ready within the reboot deadline'; exit 1; }
actual=$(ssh_vm 'cat /sys/class/dmi/id/product_uuid' | tr '[:upper:]' '[:lower:]')
[[ $actual == "$EXO_OS_VM_UUID" ]] || { result identity 0 'Reboot reached a different machine'; exit 1; }
ssh_vm 'timeout 120 systemctl is-system-running --wait'
ssh_vm 'as-exo systemctl --user stop exod'
ssh_vm 'test ! -e /var/lib/pacman/db.lck'
root=$(ssh_vm 'findmnt -n -o FSROOT /')
revision=$(ssh_vm 'cat /etc/exo-recovery-proof')
original=$(ssh_vm "cat /.snapshots/exo-recovery/checkpoints/$id/root/etc/exo-recovery-proof")
result older-root "$([[ $root == "/@snapshots/exo-recovery/roots/$id" && $revision == old-system && $original == old-system ]] && echo 1 || echo 0)" "after real reboot: root $root, system $revision, checkpoint $original"
home=$(ssh_vm 'cat /home/exo/exo-recovery-proof; findmnt -n -o FSROOT /home')
store=$(ssh_vm 'cat /var/lib/exo/exo-recovery-proof; findmnt -n -o FSROOT /var/lib/exo')
store_after=$(ssh_vm 'find /var/lib/exo/exo -type f -exec sha256sum {} + | LC_ALL=C sort | sha256sum')
result current-data "$([[ $home == newer-home/@home && $store == newer-store/@store && $store_before == "$store_after" ]] && echo 1 || echo 0)" "home/store mount original subvolumes; principal store hashes unchanged"
verify=$(ssh_vm 'as-exo /opt/exo/bin/exo log verify' | tail -1)
ledger_ok=0
if echo "$verify" | accept_verify; then ledger_ok=1; fi
result ledger-verifies "$ledger_ok" "$verify"
# Original @ remains available; selecting recovery did not promote/overwrite it.
ssh_vm 'mkdir -p /run/exo-recovery-top; mount -t btrfs -o subvolid=5,ro /dev/gpt-auto-root /run/exo-recovery-top'
original_current=$(ssh_vm 'cat /run/exo-recovery-top/@/etc/exo-recovery-proof')
ssh_vm 'umount /run/exo-recovery-top'
result original-preserved "$([[ $original_current == broken-system ]] && echo 1 || echo 0)" "original @ still contains $original_current"
accept_finish

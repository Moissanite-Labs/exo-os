#!/usr/bin/env bash
# Actual reboot acceptance, only in the harness-identified disposable vmtest VM.
# This script never mounts/modifies the host image, home, store or bootloader.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-recovery.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
pass=0 fail=0
# Probe the real service-owned journal using synthetic operation metadata only.
egress_journal_probe() {
  ssh_vm "runuser -u exo-egress -- /opt/exo/bin/python - $1" <<'PY_EGRESS_JOURNAL'
import hashlib
from pathlib import Path
import sys
from exo_os.egress.broker import Journal
from exo_os.sysd.protocol import encode
request = {"request_id": "recovery-reserved-after-checkpoint", "profile": "recovery-fixture",
           "method": "POST", "expected_origin": "https://fixture.invalid",
           "path": "/recovery-probe", "body": ""}
fingerprint = hashlib.sha256(encode(request)).hexdigest()
journal = Journal(Path("/var/lib/exo-egress/requests.sqlite"))
if sys.argv[1] == "reserve":
    assert journal.reserve(request["request_id"], fingerprint) is None
else:
    assert sys.argv[1] == "check"
with journal.connect() as db:
    row = db.execute("SELECT id,fingerprint,outcome FROM requests WHERE id=?", (request["request_id"],)).fetchone()
assert row == (request["request_id"], fingerprint, None), row
PY_EGRESS_JOURNAL
}
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
source "$here/tools/accept-common.sh"
accept_start recovery 2048
required_checks=7
accept_keyring
# Keep daemon/runtime SQLite/WAL writes out of durable-data hash comparisons.
# This mask is in @home and remains in effect across recovery of @.
ssh_vm 'as-exo systemctl --user mask --now exod.service'
ssh_vm 'test "$(as-exo systemctl --user is-enabled exod.service 2>/dev/null || true)" = masked'
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
# Start once so both startup validation and the persistent journal exist pre-snapshot.
ssh_vm 'systemctl start exo-egress.service; systemctl is-active --quiet exo-egress.service'
old_boot=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id')
ssh_vm 'test ! -e /etc/exo-recovery-proof; printf old-system > /etc/exo-recovery-proof'
checkpoint=$(ssh_vm 'exo-os-recovery checkpoint')
id=$(echo "$checkpoint" | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["id"])')
[[ $id =~ ^[0-9a-f]{32}$ ]]
# Reserve only after the older snapshot exists; rolling this back could resend.
egress_journal_probe reserve
# Newer data must survive selection of an older system snapshot.
ssh_vm 'printf newer-home > /home/exo/exo-recovery-proof; printf newer-store > /var/lib/exo/exo-recovery-proof; printf broken-system > /etc/exo-recovery-proof'
# A real helper receipt made AFTER the checkpoint must survive the older boot.
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
helper_key_before=$(ssh_vm 'sha256sum /var/lib/exo-sysd/sysd.key | cut -d " " -f 1')
helper_pub_before=$(ssh_vm 'sha256sum /run/exo-sysd/receipt.pub | cut -d " " -f 1')
helper_audit_before=$(ssh_vm 'sha256sum /var/lib/exo-sysd/audit.log | cut -d " " -f 1')
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
boot_timeout=${EXO_OS_VM_BOOT_TIMEOUT:-180}
if [[ ! $boot_timeout =~ ^[1-9][0-9]{0,2}$ ]] || ((boot_timeout > 900)); then
  result reboot 0 'EXO_OS_VM_BOOT_TIMEOUT must be a positive integer no greater than 900 seconds.'
  exit 1
fi
for attempt in $(seq 1 "$boot_timeout"); do
  kill -0 "$qemu" 2>/dev/null || { result reboot 0 'QEMU exited during recovery reboot'; exit 1; }
  current=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null) || current=''
  if [[ -n $current && $current != "$old_boot" ]]; then ready=1; break; fi
  sleep 1
done
[[ $ready == 1 ]] || { result reboot 0 'Recovery did not become ready within the reboot deadline'; exit 1; }
actual=$(ssh_vm 'cat /sys/class/dmi/id/product_uuid' | tr '[:upper:]' '[:lower:]')
[[ $actual == "$EXO_OS_VM_UUID" ]] || { result identity 0 'Reboot reached a different machine'; exit 1; }
ssh_vm 'timeout 120 systemctl is-system-running --wait'
ssh_vm 'test "$(as-exo systemctl --user is-enabled exod.service 2>/dev/null || true)" = masked'
ssh_vm 'test ! -e /var/lib/pacman/db.lck'
root=$(ssh_vm 'findmnt -n -o FSROOT /')
revision=$(ssh_vm 'cat /etc/exo-recovery-proof')
original=$(ssh_vm "cat /.snapshots/exo-recovery/checkpoints/$id/root/etc/exo-recovery-proof")
result older-root "$([[ $root == "/@snapshots/exo-recovery/roots/$id" && $revision == old-system && $original == old-system ]] && echo 1 || echo 0)" "after real reboot: root $root, system $revision, checkpoint $original"
home=$(ssh_vm 'cat /home/exo/exo-recovery-proof; findmnt -n -o FSROOT /home')
store=$(ssh_vm 'cat /var/lib/exo/exo-recovery-proof; findmnt -n -o FSROOT /var/lib/exo')
store_after=$(ssh_vm 'find /var/lib/exo/exo -type f -exec sha256sum {} + | LC_ALL=C sort | sha256sum')
result current-data "$([[ $home == newer-home/@home && $store == newer-store/@store && $store_before == "$store_after" ]] && echo 1 || echo 0)" "home/store mount original subvolumes; principal store hashes unchanged"
helper_mount=$(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-sysd')
helper_key_after=$(ssh_vm 'sha256sum /var/lib/exo-sysd/sysd.key | cut -d " " -f 1')
helper_audit_after=$(ssh_vm 'sha256sum /var/lib/exo-sysd/audit.log | cut -d " " -f 1')
# Activate the recovered helper and verify its independently published identity.
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
helper_pub_after=$(ssh_vm 'sha256sum /run/exo-sysd/receipt.pub | cut -d " " -f 1')
helper_mode=$(ssh_vm 'stat -c "%a %U %G" /var/lib/exo-sysd')
result persistent-helper "$([[ $helper_mount == /@sysd && $helper_key_after == "$helper_key_before" && $helper_pub_after == "$helper_pub_before" && $helper_audit_after == "$helper_audit_before" && $helper_mode == '700 root root' ]] && echo 1 || echo 0)" "mounted @sysd; key identity and post-checkpoint root audit preserved; helper starts on recovery root"
egress_mount=$(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-egress')
egress_journal_probe check
ssh_vm 'systemctl restart exo-egress.service; systemctl is-active --quiet exo-egress.service'
egress_mode=$(ssh_vm 'stat -c "%a %U %G" /var/lib/exo-egress')
# The real client authenticates the service process over its actual Unix socket.
# A transport fallback lacks version=1 and cannot count as a journal replay pass.
ssh_vm 'as-exo /opt/exo/bin/python -' <<'PY_EGRESS_CLIENT'
from exo_os.egress.client import EgressClient
reply = EgressClient().call({"request_id": "recovery-reserved-after-checkpoint", "profile": "recovery-fixture",
                             "method": "POST", "expected_origin": "https://fixture.invalid",
                             "path": "/recovery-probe", "body": ""})
assert reply.get("version") == 1 and reply.get("state") == "unknown" and reply.get("retryable") is False, reply
assert reply.get("message") == "Reserved operation may have completed; never resend", reply
PY_EGRESS_CLIENT
egress_journal_probe check
result persistent-egress "$([[ $egress_mount == /@egress && $egress_mode == '700 exo-egress exo-egress' ]] && echo 1 || echo 0)" "mounted @egress; post-checkpoint reservation survives older boot and service restart; authenticated replay remains unknown/nonretryable with unchanged journal row"
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

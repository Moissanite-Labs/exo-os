#!/usr/bin/env bash
# Actual reboot acceptance, only in the harness-identified disposable vmtest VM.
# This script never mounts/modifies the host image, home, store or bootloader.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd -P)"
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
authority_probe() {
  ssh_vm "as-exo /opt/exo/bin/python - $1" <<'PY_AUTHORITY'
import json
from pathlib import Path
import sys
import uuid
from exo.ledger_authority.factory import open_authority, open_writable_ledger
ledger_path = Path('/var/lib/exo/exo/principals/exo/ledger.sqlite')
proof = Path('/home/exo/.exo-recovery-authority.json')
authority = open_authority(ledger_path, 'exo-os')
if sys.argv[1] == 'append':
    assert not proof.exists(), 'recovery proof already exists'
    operation = uuid.uuid4().hex
    with open_writable_ledger(ledger_path, 'exo-os') as ledger:
        event = ledger.append('note', {'recovery_acceptance': operation}, operation_id=operation)
    record = authority.lookup(operation)
    assert record['event'] == event
    proof.write_text(json.dumps({'operation': operation, 'record': record, 'identity': authority.identity}))
else:
    assert sys.argv[1] == 'check'
    saved = json.loads(proof.read_text())
    assert saved['identity'] == authority.identity, 'authority identity changed across system recovery'
    assert authority.lookup(saved['operation']) == saved['record'], 'post-checkpoint committed record disappeared'
    assert authority.head()['seq'] >= saved['record']['checkpoint']['seq']
PY_AUTHORITY
}
spend_journal_probe() {
  ssh_vm "as-exo /opt/exo/bin/python - $1 $2" <<'PY_SPEND_JOURNAL'
import json
from pathlib import Path
import sys
from exo.model_spend import SpendJournal

policy = json.loads(Path('/var/lib/exo/model-spend/policy.json').read_text())
marker = 'recovery-acceptance-' + sys.argv[2]
if sys.argv[1] == 'freeze':
    SpendJournal(policy).freeze(marker)
else:
    assert sys.argv[1] == 'check'
status = SpendJournal.inspect(policy)
assert status['state'] == 'stopped' and status['stop_reason'] == marker, status
assert status['pending_count'] == 0, status
PY_SPEND_JOURNAL
}
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
source "$here/tools/accept-common.sh"
accept_start recovery 2048
accept_set_required_checks 9
boot_timeout=${EXO_OS_VM_BOOT_TIMEOUT:-180}
accept_keyring
# Keep daemon/runtime SQLite/WAL writes out of durable-data hash comparisons.
# This mask is in @home and remains in effect across recovery of @.
ssh_vm 'as-exo systemctl --user mask --now exod.service'
ssh_vm 'test "$(as-exo systemctl --user is-enabled exod.service 2>/dev/null || true)" = masked'
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
# Start once so both startup validation and the persistent journal exist pre-snapshot.
ssh_vm 'systemctl start exo-egress.service; systemctl is-active --quiet exo-egress.service'
ssh_vm 'systemctl start exo-ledger.service; systemctl is-active --quiet exo-ledger.service'
old_boot=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id')
ssh_vm 'test ! -e /etc/exo-recovery-proof; printf old-system > /etc/exo-recovery-proof'
checkpoint=$(ssh_vm 'exo-os-recovery checkpoint')
id=$(echo "$checkpoint" | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["id"])')
[[ $id =~ ^[0-9a-f]{32}$ ]]
# Reserve only after the older snapshot exists; rolling this back could resend.
egress_journal_probe reserve
# A record committed after the older system snapshot must stay in the protected history.
authority_probe append
# A synthetic review stop AFTER the checkpoint must remain after older-root boot.
# The VM has no paid route, key, or HTTP call in this recovery proof.
spend_journal_probe freeze "$id"
signer_identity_before=$(ssh_vm 'sha256sum /var/lib/exo-ledger/manifest.json | cut -d " " -f 1')
signer_keys_before=$(ssh_vm 'find /var/lib/exo-ledger/private -type f -name "*.key" -exec sha256sum {} + | LC_ALL=C sort | sha256sum')
spend_before=$(ssh_vm 'exo-model-spend-admin status')
spend_policy_before=$(ssh_vm 'cat /var/lib/exo/model-spend/policy.json')
spend_hash_before=$(ssh_vm 'sha256sum /var/lib/exo/model-spend/policy.json /var/lib/exo/model-spend/exo/journal.sqlite')
# Newer data must survive selection of an older system snapshot.
ssh_vm 'printf newer-home > /home/exo/exo-recovery-proof; printf newer-store > /var/lib/exo/exo-recovery-proof; printf broken-system > /etc/exo-recovery-proof'
# A real helper receipt made AFTER the checkpoint must survive the older boot.
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
helper_key_before=$(ssh_vm 'sha256sum /var/lib/exo-sysd/sysd.key | cut -d " " -f 1')
helper_pub_before=$(ssh_vm '/opt/exo/bin/python -c "import hashlib; from exo_os.sysd.protocol import PUBLIC_KEY; print(hashlib.sha256(PUBLIC_KEY.read_bytes()).hexdigest())"')
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
kill -0 "$qemu" 2>/dev/null || { result reboot 0 'QEMU exited during recovery reboot'; exit 1; }
if current=$(accept_wait_ssh "$boot_timeout" "$old_boot" 'cat /proc/sys/kernel/random/boot_id'); then
  ready=1
fi
if [[ $ready != 1 ]] && ! kill -0 "$qemu" 2>/dev/null; then
  result reboot 0 'QEMU exited during recovery reboot'
  exit 1
fi
[[ $ready == 1 ]] || { result reboot 0 'Recovery did not become ready within the reboot deadline'; exit 1; }
actual=$(ssh_vm 'cat /sys/class/dmi/id/product_uuid' | tr '[:upper:]' '[:lower:]')
[[ $actual == "$EXO_OS_VM_UUID" ]] || { result identity 0 'Reboot reached a different machine'; exit 1; }
accept_system_ready
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
signer_mount=$(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-ledger')
signer_identity_after=$(ssh_vm 'sha256sum /var/lib/exo-ledger/manifest.json | cut -d " " -f 1')
signer_keys_after=$(ssh_vm 'find /var/lib/exo-ledger/private -type f -name "*.key" -exec sha256sum {} + | LC_ALL=C sort | sha256sum')
ssh_vm 'systemctl restart exo-ledger.service; systemctl is-active --quiet exo-ledger.service'
authority_probe check
signer_mode=$(ssh_vm 'stat -c "%a %U %G" /var/lib/exo-ledger /var/lib/exo-ledger/private')
result persistent-signer "$([[ $signer_mount == /@signer && $signer_identity_before == "$signer_identity_after" && $signer_keys_before == "$signer_keys_after" && $signer_mode == $'755 root root\n700 exo-ledger exo-ledger' ]] && echo 1 || echo 0)" "mounted @signer; independent public identity, private keys and post-checkpoint committed record survive older boot and signer restart"
spend_after=$(ssh_vm 'exo-model-spend-admin status')
spend_journal_probe check "$id"
spend_policy_after=$(ssh_vm 'cat /var/lib/exo/model-spend/policy.json')
spend_hash_after=$(ssh_vm 'sha256sum /var/lib/exo/model-spend/policy.json /var/lib/exo/model-spend/exo/journal.sqlite')
spend_mode=$(ssh_vm 'stat -c "%a %U %G" /var/lib/exo/model-spend /var/lib/exo/model-spend/policy.json /var/lib/exo/model-spend/exo /var/lib/exo/model-spend/exo/journal.sqlite')
result persistent-model-spend "$([[ $spend_before == "$spend_after" && $spend_policy_before == "$spend_policy_after" && $spend_hash_before == "$spend_hash_after" && $spend_mode == $'755 root root\n644 root root\n700 exo exo\n600 exo exo' ]] && echo 1 || echo 0)" "post-checkpoint journal stop, public zero-spend policy, UUID, journal hash and strict ownership survive older root boot"
helper_mount=$(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-sysd')
helper_key_after=$(ssh_vm 'sha256sum /var/lib/exo-sysd/sysd.key | cut -d " " -f 1')
helper_audit_after=$(ssh_vm 'sha256sum /var/lib/exo-sysd/audit.log | cut -d " " -f 1')
# Activate the recovered helper and verify its independently published identity.
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
helper_pub_after=$(ssh_vm '/opt/exo/bin/python -c "import hashlib; from exo_os.sysd.protocol import PUBLIC_KEY; print(hashlib.sha256(PUBLIC_KEY.read_bytes()).hexdigest())"')
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

#!/usr/bin/env bash
# Source after the original ten recovery checks, before the final count assertion.
[[ $pass == 10 && $fail == 0 && $required_checks == 10 ]] || return 1
required_checks=11  # original ten results plus the separately named return proof
return_out=$out/recovery-return-proof
mkdir "$return_out"
printf '%s\n' "$pass" > "$return_out/original-check-count.txt"
# The original helper check already proved the post-checkpoint audit survived.
# Its recovery-root status call then appended a receipt. Preserve that complete
# audit exactly across this return reboot, rather than comparing its older hash.
helper_audit_return_before=$(ssh_vm 'sha256sum /var/lib/exo-sysd/audit.log | cut -d " " -f 1')
ssh_vm 'bootctl list --json=short' > "$return_out/boot-menu.json"
normal_entry=$("$here/.venv/bin/python" - "$return_out/boot-menu.json" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1])); ids=[r['id'] for r in rows if r.get('id','').endswith('.efi') and not r['id'].startswith('exo-recovery-')]
assert len(ids)==1,ids
print(ids[0])
PY
)
[[ $normal_entry =~ ^[A-Za-z0-9._-]+\.efi$ ]] || return 1
old_recovery_boot=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id')
ssh_vm "bootctl set-oneshot '$normal_entry'"
return_reboot_rc=0
ssh_vm 'systemctl reboot' || return_reboot_rc=$?
[[ $return_reboot_rc == 0 || $return_reboot_rc == 255 ]] || return 1
kill -0 "$qemu"
normal_boot=$(accept_wait_ssh "$boot_timeout" "$old_recovery_boot" 'cat /proc/sys/kernel/random/boot_id')
[[ $normal_boot != "$old_recovery_boot" ]] || return 1
kill -0 "$qemu"
actual=$(ssh_vm 'cat /sys/class/dmi/id/product_uuid' | tr '[:upper:]' '[:lower:]')
[[ $actual == "$EXO_OS_VM_UUID" ]] || return 1
accept_system_ready
[[ $(ssh_vm 'findmnt -n -o FSROOT /') == /@ ]] || return 1
[[ $(ssh_vm 'cat /etc/exo-recovery-proof') == broken-system ]] || return 1
[[ $(ssh_vm 'cat /home/exo/exo-recovery-proof; findmnt -n -o FSROOT /home') == newer-home/@home ]] || return 1
[[ $(ssh_vm 'cat /var/lib/exo/exo-recovery-proof; findmnt -n -o FSROOT /var/lib/exo') == newer-store/@store ]] || return 1
[[ $(ssh_vm 'find /var/lib/exo/exo -type f -exec sha256sum {} + | LC_ALL=C sort | sha256sum') == "$store_before" ]] || return 1
[[ $(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-ledger') == /@signer ]] || return 1
[[ $(ssh_vm 'stat -c "%a %U %G" /var/lib/exo-ledger /var/lib/exo-ledger/private') == $'755 root root\n700 exo-ledger exo-ledger' ]] || return 1
[[ $(ssh_vm 'sha256sum /var/lib/exo-ledger/manifest.json | cut -d " " -f 1') == "$signer_identity_before" ]] || return 1
[[ $(ssh_vm 'find /var/lib/exo-ledger/private -type f -name "*.key" -exec sha256sum {} + | LC_ALL=C sort | sha256sum') == "$signer_keys_before" ]] || return 1
ssh_vm 'systemctl start exo-ledger.service; systemctl is-active --quiet exo-ledger.service'
authority_probe check
spend_journal_probe check "$id"
[[ $(ssh_vm 'exo-model-spend-admin status') == "$spend_before" ]] || return 1
[[ $(ssh_vm 'cat /var/lib/exo/model-spend/policy.json') == "$spend_policy_before" ]] || return 1
[[ $(ssh_vm 'sha256sum /var/lib/exo/model-spend/policy.json /var/lib/exo/model-spend/exo/journal.sqlite') == "$spend_hash_before" ]] || return 1
[[ $(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-sysd') == /@sysd ]] || return 1
[[ $(ssh_vm 'stat -c "%a %U %G" /var/lib/exo-sysd') == '700 root root' ]] || return 1
[[ $(ssh_vm 'sha256sum /var/lib/exo-sysd/sysd.key | cut -d " " -f 1') == "$helper_key_before" ]] || return 1
[[ $(ssh_vm 'sha256sum /var/lib/exo-sysd/audit.log | cut -d " " -f 1') == "$helper_audit_return_before" ]] || return 1
ssh_vm 'as-exo /opt/exo/bin/exo-os system status >/dev/null'
[[ $(ssh_vm '/opt/exo/bin/python -c "import hashlib; from exo_os.sysd.protocol import PUBLIC_KEY; print(hashlib.sha256(PUBLIC_KEY.read_bytes()).hexdigest())"') == "$helper_pub_before" ]] || return 1
[[ $(ssh_vm 'findmnt -n -o FSROOT /var/lib/exo-egress') == /@egress ]] || return 1
[[ $(ssh_vm 'stat -c "%a %U %G" /var/lib/exo-egress') == '700 exo-egress exo-egress' ]] || return 1
ssh_vm 'systemctl start exo-egress.service; systemctl is-active --quiet exo-egress.service'
egress_journal_probe check
ssh_vm 'as-exo /opt/exo/bin/exo log verify' | tail -1 | accept_verify
# No private key bytes or key hashes are written into this record.
"$here/.venv/bin/python" - "$return_out/result.json" "$id" "$old_recovery_boot" "$normal_boot" "$EXO_OS_VM_UUID" "$normal_entry" <<'PY'
import json,sys,pathlib
pathlib.Path(sys.argv[1]).write_text(json.dumps({'result':'pass','checkpoint_id':sys.argv[2],'recovery_boot_id':sys.argv[3],'normal_boot_id':sys.argv[4],'vm_uuid':sys.argv[5],'normal_entry':sys.argv[6],'root':'/@','root_marker':'broken-system','preserved':['home','store','signer','committed authority','helper','spend stop/policy/journal','egress reservation'],'original_checks':10,'return_checks':1,'required_total_checks':11,'return_checks_separate':True},indent=2)+'\n')
PY
printf '%s\n' 'return-to-normal: PASS (separate from original10checks)' | tee "$return_out/RESULT"
result return-to-normal 1 "real reboot returned to original @; VM identity and home/store/signer/helper/spend/egress state preserved"

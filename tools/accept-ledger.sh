#!/usr/bin/env bash
# Actual protected signer and tamper detection, only in a disposable vmtest image.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-ledger.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { accept_as_exo "$@"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
source "$here/tools/accept-common.sh"
accept_start ledger 2048
accept_set_required_checks 11
daemon_env=$(as_exo 'systemctl --user show exod -p Environment --value')
required=0
if [[ " $daemon_env " == *' EXO_LEDGER_AUTHORITY=exo-os '* && " $daemon_env " == *' EXO_LEDGER_AUTHORITY_REQUIRED=1 '* ]]; then required=1; fi
result authority-required "$required" 'installed exod service requires the protected OS authority'
# Quiesce the writer before changing its disposable local projection.
as_exo 'systemctl --user mask --now exod.service'
ssh_vm 'systemctl is-active --quiet exo-ledger-provision.service'
ssh_vm 'cat > /tmp/probe-ledger.py && chmod 0644 /tmp/probe-ledger.py' < "$here/tools/probe-ledger.py"

run_probe() {
  local mode=$1 rc=0 output parsed
  shift
  output=$(as_exo "EXO_OS_VM_ACCEPTANCE=1 /opt/exo/bin/python /tmp/probe-ledger.py --vm-acceptance $mode") || rc=$?
  printf '%s\n' "$output" > "$out/ledger-probe${mode:+-unavailable}.json"
  if [[ $rc == 0 || $rc == 1 ]] && parsed=$(printf '%s\n' "$output" | "$here/.venv/bin/python" -c '
import json,sys
expected=sys.argv[1:-1]; rc=int(sys.argv[-1]); checks=json.load(sys.stdin).get("checks")
if not isinstance(checks,list) or [x.get("name") for x in checks] != expected: raise SystemExit(2)
if (rc == 0) != all(x.get("status") == "PASS" for x in checks): raise SystemExit(3)
for item in checks:
    if item.get("status") not in ("PASS","FAIL") or not isinstance(item.get("detail"),str): raise SystemExit(4)
    print(item["name"]+"\t"+item["status"]+"\t"+item["detail"].replace("\t"," ").replace("\n"," ")[:240])
' "$@" "$rc"); then
    while IFS=$'\t' read -r name status detail; do
      result "$name" "$([[ $status == PASS ]] && echo 1 || echo 0)" "$detail"
    done <<< "$parsed"
  else
    for name in "$@"; do result "$name" 0 "probe did not return the exact JSON contract (exit $rc)"; done
  fi
}
run_probe '' private_state_unreadable append_with_service_identity idempotent_operation \
  truncation_detected explicit_projection_recovery edited_row_detected unconfirmed_tail_detected direct_key_write_refused
# systemd leaves a stale filesystem socket by default (RemoveOnStop=no). Require
# both units to be inactive; the real client probes below prove nothing listens.
ssh_vm 'systemctl stop exo-ledger.socket exo-ledger.service; test "$(systemctl is-active exo-ledger.socket)" = inactive; test "$(systemctl is-active exo-ledger.service)" = inactive'
run_probe --signer-unavailable missing_signer_refuses_append missing_signer_refuses_verification
ssh_vm 'systemctl start exo-ledger.socket'
echo '      scope: actual local signer and user projection; no off-machine witness is claimed' | tee -a "$report"
accept_finish

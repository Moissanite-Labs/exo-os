#!/usr/bin/env bash
# Protected zero-spend acceptance in a provenance-matched disposable vmtest VM.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-model-spend.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { ssh_vm "cd /tmp && as-exo $*"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
source "$here/tools/accept-common.sh"
accept_start model-spend 2048
accept_set_required_checks 9
wait_exod() {
  local ready=0
  for _ in {1..90}; do
    if as_exo 'systemctl --user is-active --quiet exod.service && test -S /var/lib/exo/exo/run/exod.sock'; then
      ready=1
      break
    fi
    sleep 2
  done
  [[ $ready == 1 ]]
}
ssh_vm 'systemctl is-active --quiet exo-model-spend-provision.service'
env=$(as_exo 'systemctl --user show exod -p Environment --value')
required=0
if [[ " $env " == *' EXO_MODEL_SPEND_REQUIRED=1 '* && " $env " == *' EXO_MODEL_SPEND_POLICY=/var/lib/exo/model-spend/policy.json '* ]]; then required=1; fi
result daemon-protected "$required" 'installed exod requires the fixed root policy'

# Only the disposable overlay is changed. No paid route, key or network endpoint is configured.
as_exo "/opt/exo/bin/python - <<'PY'
import json
from pathlib import Path
path=Path('/var/lib/exo/exo/config.json')
raw=json.loads(path.read_text())
raw['models']={'fake': {'reply': 'Offline spend acceptance reply.'},
               'roles': {role: ['fake:scripted'] for role in
                         ('main','aux','decision','eval','fast','standard','deep')}}
path.write_text(json.dumps(raw, indent=2))
PY"
as_exo 'systemctl --user restart exod.service'
wait_exod
ssh_vm 'cat > /tmp/probe-model-spend.py && chmod 0644 /tmp/probe-model-spend.py' < "$here/tools/probe-model-spend.py"

run_probe() {
  local mode=$1 rc=0 output parsed
  shift
  output=$(as_exo "EXO_OS_VM_ACCEPTANCE=1 /opt/exo/bin/python /tmp/probe-model-spend.py --vm-acceptance $mode") || rc=$?
  printf '%s\n' "$output" > "$out/model-spend-probe${mode:+-stopped}.json"
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

before=$(ssh_vm 'exo-model-spend-admin status' | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["journal_id"])')
run_probe '' zero_policy offline_turn mutable_caps_refused paid_route_no_http
ssh_vm 'systemctl restart exo-model-spend-provision.service; systemctl is-active --quiet exo-model-spend-provision.service'
as_exo 'systemctl --user restart exod.service'
wait_exod
after=$(ssh_vm 'exo-model-spend-admin status' | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["journal_id"])')
result persistent-id "$([[ $before == "$after" ]] && echo 1 || echo 0)" 'the same journal UUID survived provision-unit and daemon restarts'

# The refused paid route already recorded the stop; restart and inspect it.
as_exo 'systemctl --user restart exod.service'
wait_exod
run_probe --stopped stopped_status stopped_generation transcripts_readable
echo '      scope: offline RuleFake and mocked HTTP refusal; no paid call or account-wide billing claim' | tee -a "$report"
accept_finish

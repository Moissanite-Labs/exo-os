#!/usr/bin/env bash
# Protected zero-spend acceptance in a provenance-matched disposable vmtest VM.
set -Eeuo pipefail

# A tools-only harness revision can inspect an earlier image only when the
# operator names that exact image and the installed runtime digest still matches.
[[ $# == 3 && ${1:-} == --vm-acceptance && ${2:-} == --image-source \
   && ${3:-} =~ ^[0-9a-f]{40}$ && ${EXO_OS_VM_ACCEPTANCE:-} == 1 ]] || {
  echo 'requires --vm-acceptance --image-source FULL_40_HEX_HASH and EXO_OS_VM_ACCEPTANCE=1' >&2
  exit 2
}
image_source=$3

here="$(cd "$(dirname "$0")/.." && pwd -P)"
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
harness_source=$(python3 "$here/tools/source_identity.py" \
  --source-root "$here" --require-clean --label "model-spend harness source") || exit 2
manifest="$out/extra/opt/exo/EXO_OS_BUILD.json"
[[ -s $manifest ]] || {
  echo 'the selected image build manifest is missing' >&2
  exit 2
}
if ! "$here/.venv/bin/python" - "$manifest" "$image_source" <<'PY_IMAGE_SOURCE'
import json
import sys

try:
    with open(sys.argv[1]) as stream:
        built = json.load(stream)
    valid = (built.get("schema") == 2 and built.get("build_profile") == "vmtest"
             and built.get("exo_os_commit") == sys.argv[2])
except (OSError, ValueError, AttributeError):
    valid = False
raise SystemExit(0 if valid else 1)
PY_IMAGE_SOURCE
then
  echo "model-spend acceptance requires a vmtest image manifest built from $image_source" >&2
  exit 2
fi
accept_start model-spend 2048
echo "      image source: $image_source; model-spend harness source: $harness_source" | tee -a "$report"
accept_set_required_checks 11
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

# Only the disposable overlay is changed. The daemon uses an offline fake;
# the separate priced-route probe below has a mock HTTP transport and no key.
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
policy_hash_before=$(ssh_vm 'sha256sum /var/lib/exo/model-spend/policy.json | cut -d " " -f 1')

# The disposable VM gets a second root-controlled policy solely for the
# mocked priced-route probe. The installed persistent zero-default stays intact.
priced_policy=$(ssh_vm "/opt/exo/bin/python - <<'PY'
import json
import os
import tempfile
from pathlib import Path
from exo.model_spend import decode_policy, validate_policy
source = Path('/var/lib/exo/model-spend/policy.json')
raw = decode_policy(source.read_bytes())
assert raw['principal'] == 'exo' and raw['journal'] == '/var/lib/exo/model-spend/exo/journal.sqlite'
assert raw['daily_usd'] == raw['monthly_usd'] == '0' and raw['routes'] == {}
directory = Path(tempfile.mkdtemp(prefix='exo-model-spend-', dir='/run'))
os.chmod(directory, 0o755)
path = directory / 'policy.json'
ref = 'native:gpt-4.1-2025-04-14'
priced = {**raw, 'routes': {ref: {
    'contract': 'text-tokens-v1', 'transport': 'openai',
    'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4.1-2025-04-14',
    'input_bound': 1047576, 'max_output': 100,
    'input': '1', 'output': '10', 'cache_read': '1', 'cache_write': '1'}}}
validate_policy(priced)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
with os.fdopen(fd, 'w') as stream:
    json.dump(priced, stream, sort_keys=True)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())
os.chmod(path, 0o644)
for item in (directory, Path('/run')):
    sync_fd = os.open(item, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(sync_fd)
    finally: os.close(sync_fd)
print(path)
PY")
[[ $priced_policy =~ ^/run/exo-model-spend-[A-Za-z0-9_-]+/policy\.json$ ]]

run_probe() {
  local mode=$1 rc=0 output parsed
  shift
  output=$(as_exo "EXO_OS_VM_ACCEPTANCE=1 EXO_OS_VM_PRICED_POLICY=$priced_policy EXO_MODEL_SPEND_REQUIRED=1 EXO_MODEL_SPEND_POLICY=/var/lib/exo/model-spend/policy.json /opt/exo/bin/python /tmp/probe-model-spend.py --vm-acceptance $mode") || rc=$?
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
run_probe '' zero_policy offline_turn mutable_caps_refused paid_route_no_http priced_zero_cap_no_http offline_after_refusal
# Re-run the root administrator validator directly. Restarting this boot-order
# unit tears down ordered greetd and the live exo user session.
ssh_vm 'exo-model-spend-admin ensure-boot'
as_exo 'systemctl --user restart exod.service'
wait_exod
after=$(ssh_vm 'exo-model-spend-admin status' | "$here/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["journal_id"])')
policy_hash_after=$(ssh_vm 'sha256sum /var/lib/exo/model-spend/policy.json | cut -d " " -f 1')
result persistent-id "$([[ $before == "$after" && $policy_hash_before == "$policy_hash_after" ]] && echo 1 || echo 0)" 'the same journal UUID and persistent zero-default policy survived root revalidation and daemon restart'

# Simulate an uncertain incurred call only in this disposable VM. This is a
# deliberate fixture, not evidence that any provider charged the account.
as_exo "/opt/exo/bin/python - <<'PY'
import json
from pathlib import Path
from exo.model_spend import SpendJournal
policy=json.loads(Path('/var/lib/exo/model-spend/policy.json').read_text())
SpendJournal(policy).freeze('VM synthetic uncertain spend fixture; no provider call')
PY"
as_exo 'systemctl --user restart exod.service'
wait_exod
run_probe --stopped stopped_status stopped_generation transcripts_readable
echo '      scope: offline RuleFake and mocked HTTP unpriced/zero-cap refusals; no paid call or account-wide billing claim' | tee -a "$report"
accept_finish

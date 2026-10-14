#!/usr/bin/env bash
# Phase 4 kernel isolation acceptance. Runs only in the throwaway vmtest VM, never on the host.
# Requires a freshly built image containing the sandbox package, bubblewrap and libseccomp.
# The probe writes only uniquely named synthetic files under the configured workspace and its parent.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/accept-isolation.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { ssh_vm "cd /tmp && as-exo $*"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}

source "$here/tools/accept-common.sh"
accept_start 4 1536
# Two daemon checks, the installed spend-state prerequisite, and nine kernel probes.
accept_set_required_checks 12

daemon_state=$(as_exo 'systemctl --user is-active exod' 2>&1 || true)
result "exod-active" "$([[ $daemon_state == active ]] && echo 1 || echo 0)" "systemctl --user is-active exod: ${daemon_state:-empty}"
daemon_env=$(as_exo 'systemctl --user show exod -p Environment --value' 2>&1 || true)
required_env=0
if [[ " $daemon_env " == *' EXO_TOOL_EXECUTOR=exo-os '* && " $daemon_env " == *' EXO_TOOL_EXECUTOR_REQUIRED=1 '* ]]; then required_env=1; fi
result "executor-required" "$required_env" "service Environment contains EXO_TOOL_EXECUTOR=exo-os and EXO_TOOL_EXECUTOR_REQUIRED=1"

# A sandbox refusal proves nothing if the target files were missing to begin with.
spend_state=missing
if spend_state=$(ssh_vm 'python3 -' <<'PY_SPEND_STATE'
import pwd
import stat
from pathlib import Path

uid = pwd.getpwnam('exo').pw_uid
gid = pwd.getpwnam('exo').pw_gid
paths = (
    ('/var/lib/exo', stat.S_IFDIR, 0, 0, 0o755),
    ('/var/lib/exo/model-spend', stat.S_IFDIR, 0, 0, 0o755),
    ('/var/lib/exo/model-spend/policy.json', stat.S_IFREG, 0, 0, 0o644),
    ('/var/lib/exo/model-spend/exo', stat.S_IFDIR, uid, gid, 0o700),
    ('/var/lib/exo/model-spend/exo/journal.sqlite', stat.S_IFREG, uid, gid, 0o600),
)
for name, kind, owner, group, mode in paths:
    info = Path(name).lstat()
    assert stat.S_IFMT(info.st_mode) == kind, name
    assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (owner, group, mode), name
print('present')
PY_SPEND_STATE
); then :; fi
result "model-spend-state-present" "$([[ $spend_state == present ]] && echo 1 || echo 0)" "installed policy and journal exist as regular files with expected owners and modes"
[[ $spend_state == present ]] || exit 1

# Transfer a standalone probe into the VM. It refuses to run without both explicit opt-ins and the exo UID.
ssh_vm 'cat > /tmp/probe-isolation.py && chmod 0644 /tmp/probe-isolation.py' < "$here/tools/probe-isolation.py"
probe_output=''
probe_rc=1
if probe_output=$(as_exo 'EXO_OS_VM_ACCEPTANCE=1 /opt/exo/bin/python /tmp/probe-isolation.py --vm-acceptance' 2>&1); then
  probe_rc=0
fi
printf '%s\n' "$probe_output" > "$out/isolation-probe.json"
expected=(workspace_read_write outside_secret_and_symlink network_socket_denied unix_socket_denied runtime_write_denied model_spend_state_denied timeout_kills_grandchild cancel_kills_grandchild missing_bwrap_fails_closed)
parsed=''
if [[ $probe_rc == 0 || $probe_rc == 1 ]] && parsed=$(printf '%s\n' "$probe_output" | "$here/.venv/bin/python" -c '
import json,sys
expected = sys.argv[1:-1]
rc = int(sys.argv[-1])
obj = json.load(sys.stdin)
checks = obj.get("checks")
if not isinstance(checks,list) or [x.get("name") for x in checks] != expected: raise SystemExit(2)
if (rc == 0) != all(x.get("status") == "PASS" for x in checks): raise SystemExit(4)
for x in checks:
    if x.get("status") not in ("PASS","FAIL") or not isinstance(x.get("detail"),str): raise SystemExit(3)
    detail = x["detail"].replace("\t"," ").replace("\n"," ")[:240]
    print(x["name"] + "\t" + x["status"] + "\t" + detail)
' "${expected[@]}" "$probe_rc" 2>/dev/null); then
  while IFS=$'\t' read -r name status detail; do
    [[ -n $name ]] || continue
    result "$name" "$([[ $status == PASS ]] && echo 1 || echo 0)" "$detail"
  done <<< "$parsed"
else
  for name in "${expected[@]}"; do result "$name" 0 "probe did not return the exact nine-check JSON contract (exit $probe_rc)"; done
fi

# Keep this evidence narrow: no outbound credential broker or signer is exercised by this probe.
echo "      scope: local Linux filesystem/process/network isolation only; egress broker and signer not tested" | tee -a "$report"
accept_finish

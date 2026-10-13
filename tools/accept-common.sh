#!/usr/bin/env bash
# Shared fail-closed harness for destructive checks inside a throwaway Exo OS VM.
# Source after defining here, out, report, ssh_vm and result.
set -Eeuo pipefail

accept_error() {
  local rc=$1 line=$2
  trap - ERR
  result harness 0 "required command failed at line $line (exit $rc)"
  exit "$rc"
}
trap 'accept_error "$?" "$LINENO"' ERR

accept_cleanup() {
  if [[ -n ${qemu:-} ]]; then
    kill "$qemu" 2>/dev/null || true
    wait "$qemu" 2>/dev/null || true
  fi
}
trap accept_cleanup EXIT

accept_set_required_checks() {
  # Custom suites count their own result rows; add the provenance result from accept_start.
  required_checks=$(($1 + 1))
}

accept_wait_ssh() {
  local timeout=$1 expected=$2
  shift 2
  "$here/.venv/bin/python" "$here/tools/accept-wait-ssh.py" "$timeout" "$expected" "$@"
}

accept_require_vmtest_profile() {
  local manifest="$out/extra/opt/exo/EXO_OS_BUILD.json"
  if [[ ! -s $manifest ]] || ! "$here/.venv/bin/python" - "$manifest" <<'PY_PROFILE'
import json
import sys

try:
    with open(sys.argv[1]) as stream:
        built = json.load(stream)
    valid = built.get("schema") == 2 and built.get("build_profile") == "vmtest"
except (OSError, ValueError, AttributeError):
    valid = False
raise SystemExit(0 if valid else 1)
PY_PROFILE
  then
    result prerequisites 0 'Acceptance requires a manifest from tools/build-image.sh --profile=vmtest before QEMU starts.'
    exit 1
  fi
}

accept_system_ready() {
  local state rc=0
  state=$(ssh_vm "timeout $ready_timeout systemctl is-system-running --wait") || rc=$?
  if ((rc != 0)) || [[ $state != running ]]; then
    ssh_vm 'systemctl is-system-running; systemctl list-jobs --no-pager; systemctl --failed --no-pager' >&2 || true
    result readiness 0 "systemd did not reach running within ${ready_timeout}s (last state: ${state:-unavailable})."
    exit 1
  fi
}

accept_start() {
  local phase=$1 memory=$2 connected=0 boot_timeout=${EXO_OS_VM_BOOT_TIMEOUT:-180}
  ready_timeout=${EXO_OS_VM_READY_TIMEOUT:-300}
  required_checks=6
  [[ $phase != 2 ]] || required_checks=7
  mkdir -p "$out"
  : > "$report"
  [[ -s $out/exo-os.raw && -x $here/.venv/bin/python ]] || {
    result prerequisites 0 'Build the vmtest image and run tools/dev-env.sh first.'
    exit 1
  }
  accept_require_vmtest_profile
  if [[ ! $boot_timeout =~ ^[1-9][0-9]{0,2}$ ]] || ((boot_timeout > 900)); then
    result prerequisites 0 'EXO_OS_VM_BOOT_TIMEOUT must be a positive integer no greater than 900 seconds.'
    exit 1
  fi
  if [[ ! $ready_timeout =~ ^[1-9][0-9]{0,2}$ ]] || ((ready_timeout > 900)); then
    result prerequisites 0 'EXO_OS_VM_READY_TIMEOUT must be a positive integer no greater than 900 seconds.'
    exit 1
  fi
  export here out
  export -f ssh_vm
  local source_commit
  if source_commit=$(git -C "$here" rev-parse HEAD 2>/dev/null); then
    :
  elif [[ -s $here/.EXO_OS_SOURCE_ID ]]; then
    source_commit=$(tr -d '\n' < "$here/.EXO_OS_SOURCE_ID")
  else
    source_commit=${EXO_OS_SOURCE_COMMIT:-}
  fi
  if [[ ! $source_commit =~ ^[0-9a-f]{40}$ ]]; then
    result prerequisites 0 'Source identity is missing; use a Git checkout or an archive with .EXO_OS_SOURCE_ID.'
    exit 1
  fi
  echo "Exo OS phase $phase acceptance, $(date -u +%Y-%m-%dT%H:%M:%SZ), exo $(cut -c1-7 "$here/exo.pin"), exo-os ${source_commit:0:7}" | tee -a "$report"
  EXO_OS_VM_UUID=$("$here/.venv/bin/python" -c 'import uuid; print(uuid.uuid4())')
  export EXO_OS_VM_UUID
  local vm_args=(--mem "${EXO_OS_VM_MEM:-$memory}") extra_args=()
  if [[ -n ${EXO_OS_VM_ARGS:-} ]]; then
    read -r -a extra_args <<< "$EXO_OS_VM_ARGS"
    for arg in "${extra_args[@]}"; do
      if [[ $arg == --keep ]]; then
        result prerequisites 0 'Acceptance requires a disposable overlay; EXO_OS_VM_ARGS must not contain --keep.'
        exit 1
      fi
    done
    vm_args+=("${extra_args[@]}")
  fi
  "$here/tools/run-vm.sh" "${vm_args[@]}" > "$out/qemu.log" 2>&1 &
  qemu=$!
  kill -0 "$qemu" 2>/dev/null || {
    result boot 0 'QEMU exited; see image/out/qemu.log.'
    exit 1
  }
  if accept_wait_ssh "$boot_timeout" - true; then connected=1; fi
  if [[ $connected != 1 ]]; then
    result boot 0 'SSH did not become ready within the boot deadline.'
    exit 1
  fi
  # A service already on port 2222 must never become the target of these checks.
  local actual
  actual=$(ssh_vm 'cat /sys/class/dmi/id/product_uuid' | tr '[:upper:]' '[:lower:]')
  if [[ $actual != "$EXO_OS_VM_UUID" ]]; then
    result identity 0 'The connected machine is not the throwaway VM started by this run.'
    exit 1
  fi
  accept_system_ready
  # First-boot keyring initialization creates and removes lock files under
  # /etc/pacman.d. Audit file modes only after those mutations have settled.
  accept_provenance
}

accept_provenance() {
  local pin image_pin expected="$out/extra/opt/exo/EXO_OS_BUILD.json" current actual built_commit
  pin=$(tr -d '\n' < "$here/exo.pin")
  image_pin=$(ssh_vm 'cat /opt/exo/EXO_PIN') || {
    result provenance 0 'The booted image has no installed Exo pin.'
    exit 1
  }
  if [[ $image_pin != "$pin" ]]; then
    result provenance 0 "Image Exo pin $image_pin differs from repository pin $pin."
    exit 1
  fi
  if [[ ! -s $expected ]]; then
    result provenance 0 'Build provenance is missing; rebuild the image with tools/build-image.sh.'
    exit 1
  fi
  current=$(mktemp "$out/.provenance.XXXXXX")
  "$here/.venv/bin/python" "$here/tools/build-provenance.py" \
    --source-root "$here" --pin "$pin" --profile vmtest --output "$current"
  if ! "$here/.venv/bin/python" - "$expected" "$current" <<'PY_PROVENANCE_COMPARE'
import json
import sys

with open(sys.argv[1]) as stream:
    built = json.load(stream)
with open(sys.argv[2]) as stream:
    current = json.load(stream)
fields = ("schema", "exo_pin", "source_sha256", "build_profile")
raise SystemExit(0 if all(built.get(key) == current.get(key) for key in fields) else 1)
PY_PROVENANCE_COMPARE
  then
    rm -f "$current"
    result provenance 0 'The source or image configuration changed after this image was built.'
    exit 1
  fi
  built_commit=$("$here/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["exo_os_commit"])' "$expected")
  actual=$(ssh_vm 'cat /opt/exo/EXO_OS_BUILD.json') || {
    rm -f "$current"
    result provenance 0 'The booted image has no build provenance manifest.'
    exit 1
  }
  if ! printf '%s\n' "$actual" | cmp -s - "$expected"; then
    rm -f "$current"
    result provenance 0 'The booted image build manifest differs from the local build manifest.'
    exit 1
  fi
  local mode_script mode_encoded mode_result
  mode_script='import os,stat,sys
paths=("/","/etc","/etc/exo-os","/etc/exo-os/tools.toml","/etc/exo-os/egress.toml","/etc/greetd","/etc/pacman.d","/etc/profile.d","/etc/skel","/opt","/opt/exo","/opt/exo/plugins","/usr","/usr/lib","/usr/local","/usr/local/bin","/usr/lib/exo-os","/usr/lib/systemd","/usr/lib/systemd/system","/usr/lib/environment.d")
for path in paths:
    if os.path.lexists(path):
        info=os.lstat(path)
        if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
            raise SystemExit("unsafe mode or owner: %s uid=%d gid=%d mode=%o" % (path, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)))
for top in ("/etc/exo-os","/etc/greetd","/etc/pacman.d","/etc/profile.d","/etc/skel","/opt/exo","/usr/lib/systemd/system","/usr/lib/systemd/system-preset","/usr/lib/systemd/user","/usr/lib/systemd/user-preset","/usr/lib/environment.d"):
    if os.path.isdir(top):
        for base, dirs, files in os.walk(top, followlinks=False):
            for name in dirs + files:
                path=os.path.join(base,name)
                if os.path.islink(path): continue
                info=os.lstat(path)
                if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
                    raise SystemExit("unsafe imported path: %s" % path)
print("ok")'
  mode_encoded=$(printf '%s' "$mode_script" | base64 | tr -d '\n')
  mode_result=$(ssh_vm "python3 -c \"\$(printf '%s' '$mode_encoded' | base64 -d)\"") || {
    rm -f "$current"
    result provenance 0 'The image contains group-writable or non-root-owned administrator/runtime paths.'
    exit 1
  }
  if [[ $mode_result != ok ]]; then
    rm -f "$current"
    result provenance 0 'The image mode audit did not return its success marker.'
    exit 1
  fi
  rm -f "$current"
  result provenance 1 "image Exo pin and source digest match build commit ${built_commit:0:7}."
}

accept_keyring() {
  # exo-os-pacman-init.service is a oneshot: systemd reports it "activating" while it runs, and
  # "inactive" both after a successful run and when its condition skipped it (for example because a
  # keyring was already in the image). Wait for a recorded successful run on this boot, not for a keyring.
  local wait
  wait='u=exo-os-pacman-init.service; n=0; until [ "$(systemctl show -p ActiveState --value $u)" = inactive ] && [ "$(systemctl show -p Result --value $u)" = success ] && [ "$(systemctl show -p ExecMainExitTimestampMonotonic --value $u)" != 0 ]; do n=$((n+1)); [ $n -le 60 ] || exit 1; sleep 2; done'
  if ! ssh_vm "sh -c '$wait'" || ! ssh_vm 'test -s /etc/pacman.d/gnupg/trustdb.gpg'; then
    ssh_vm 'systemctl show -p ConditionResult,ActiveState,Result,ExecMainExitTimestampMonotonic exo-os-pacman-init.service; ls -ld /etc/pacman.d/gnupg' >&2 || true
    result keyring 0 'The first-boot keyring unit did not run and finish on this boot, or left no keyring. A keyring already in the image would skip it.'
    exit 1
  fi
}

accept_verify() {
  "$here/.venv/bin/python" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") is True else 1)'
}

accept_finish() {
  if ((pass + fail != required_checks)); then
    result harness 0 "Expected $required_checks checks, observed $((pass + fail))."
  fi
  echo "passed $pass, failed $fail" | tee -a "$report"
  ((fail == 0))
}

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

accept_start() {
  local phase=$1 memory=$2 attempt connected=0
  required_checks=5
  [[ $phase != 2 ]] || required_checks=6
  mkdir -p "$out"
  : > "$report"
  [[ -s $out/exo-os.raw && -x $here/.venv/bin/python ]] || {
    result prerequisites 0 'Build the vmtest image and run tools/dev-env.sh first.'
    exit 1
  }
  echo "Exo OS phase $phase acceptance, $(date -u +%Y-%m-%dT%H:%M:%SZ), exo $(cut -c1-7 "$here/exo.pin"), exo-os $(git -C "$here" rev-parse --short HEAD)" | tee -a "$report"
  EXO_OS_VM_UUID=$("$here/.venv/bin/python" -c 'import uuid; print(uuid.uuid4())')
  export EXO_OS_VM_UUID
  local vm_args=(--mem "${EXO_OS_VM_MEM:-$memory}") extra_args=()
  if [[ -n ${EXO_OS_VM_ARGS:-} ]]; then
    read -r -a extra_args <<< "$EXO_OS_VM_ARGS"
    vm_args+=("${extra_args[@]}")
  fi
  "$here/tools/run-vm.sh" "${vm_args[@]}" > "$out/qemu.log" 2>&1 &
  qemu=$!
  for attempt in $(seq 1 60); do
    kill -0 "$qemu" 2>/dev/null || {
      result boot 0 'QEMU exited; see image/out/qemu.log.'
      exit 1
    }
    if ssh_vm true 2>/dev/null; then connected=1; break; fi
    sleep 3
  done
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
  ssh_vm 'timeout 120 systemctl is-system-running --wait'
}

accept_keyring() {
  ssh_vm 'timeout 120 sh -c '\''until [ -e /etc/pacman.d/gnupg/trustdb.gpg ] && ! systemctl is-active -q exo-os-pacman-init; do sleep 2; done'\'''
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

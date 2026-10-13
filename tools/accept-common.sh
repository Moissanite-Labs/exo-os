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
  local phase=$1 memory=$2 attempt connected=0 boot_timeout=${EXO_OS_VM_BOOT_TIMEOUT:-180}
  required_checks=6
  [[ $phase != 2 ]] || required_checks=7
  mkdir -p "$out"
  : > "$report"
  [[ -s $out/exo-os.raw && -x $here/.venv/bin/python ]] || {
    result prerequisites 0 'Build the vmtest image and run tools/dev-env.sh first.'
    exit 1
  }
  if [[ ! $boot_timeout =~ ^[1-9][0-9]{0,2}$ ]] || ((boot_timeout > 900)); then
    result prerequisites 0 'EXO_OS_VM_BOOT_TIMEOUT must be a positive integer no greater than 900 seconds.'
    exit 1
  fi
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
  for attempt in $(seq 1 "$boot_timeout"); do
    kill -0 "$qemu" 2>/dev/null || {
      result boot 0 'QEMU exited; see image/out/qemu.log.'
      exit 1
    }
    if ssh_vm true 2>/dev/null; then connected=1; break; fi
    sleep 1
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
  accept_provenance
  ssh_vm 'timeout 120 systemctl is-system-running --wait'
}

accept_provenance() {
  local pin image_pin expected="$out/extra/opt/exo/EXO_OS_BUILD.json" current actual
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
    --source-root "$here" --pin "$pin" --output "$current"
  if ! cmp -s "$expected" "$current"; then
    rm -f "$current"
    result provenance 0 'The source or image configuration changed after this image was built.'
    exit 1
  fi
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
  rm -f "$current"
  result provenance 1 "image Exo pin and source digest match $(git -C "$here" rev-parse --short HEAD)."
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

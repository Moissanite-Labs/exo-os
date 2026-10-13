#!/usr/bin/env bash
# Optional, separate AT-SPI diagnostic. It does not alter any acceptance suite's checks.
set -Eeuo pipefail

# Refuse before touching the image, creating output, or starting QEMU.
[[ ${1:-} == --vm-acceptance && $# == 1 && ${EXO_OS_VM_ACCEPTANCE:-} == 1 ]] || {
  echo 'requires --vm-acceptance and EXO_OS_VM_ACCEPTANCE=1' >&2
  exit 2
}

here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/image/out"
report="$out/diagnose-atspi.txt"
ssh_vm() { "$here/tools/vm-ssh.sh" "$@"; }
as_exo() { ssh_vm "cd /tmp && as-exo $*"; }
pass=0 fail=0
result() {
  local line
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); line="PASS  $1  $3"; else fail=$((fail + 1)); line="FAIL  $1  $3"; fi
  echo "$line" | tee -a "$report"
}
source "$here/tools/accept-common.sh"
surface_stopped=0
diagnostic_cleanup() {
  if [[ $surface_stopped == 1 ]]; then
    as_exo 'systemctl --user start exo-surface.service' >/dev/null 2>&1 || true
  fi
  accept_cleanup
}
trap diagnostic_cleanup EXIT

# This image was built from the published accessibility revision. Tools-only
# diagnostic changes have their own revision, while the source digest must
# still match the installed image through accept_provenance.
image_source=e9cec25f63154fb15af72de43c6a9d14c74324bc
if [[ $(git -C "$here" rev-parse --show-toplevel 2>/dev/null || true) == "$here" ]]; then
  diagnostic_source=$(git -C "$here" rev-parse HEAD)
  [[ -z $(git -C "$here" status --porcelain) ]] || {
    echo 'diagnostic source must be a clean committed checkout' >&2
    exit 2
  }
elif [[ -s $here/.EXO_OS_SOURCE_ID ]]; then
  diagnostic_source=$(tr -d '\n' < "$here/.EXO_OS_SOURCE_ID")
else
  echo 'diagnostic source must be a Git checkout or archive with .EXO_OS_SOURCE_ID' >&2
  exit 2
fi
[[ $diagnostic_source =~ ^[0-9a-f]{40}$ ]] || {
  echo 'diagnostic source identifier is malformed' >&2
  exit 2
}
manifest="$out/extra/opt/exo/EXO_OS_BUILD.json"
[[ -s $manifest ]] || {
  echo 'the published e9 image build manifest is missing' >&2
  exit 2
}
built_commit=$("$here/.venv/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["exo_os_commit"])' "$manifest")
[[ $built_commit == "$image_source" ]] || {
  echo "diagnostic requires image built from $image_source; found $built_commit" >&2
  exit 2
}

accept_start 3 1536  # authenticates throwaway UUID and installed runtime/image provenance
accept_set_required_checks 1  # this separate diagnostic adds one check to provenance
as_exo 'systemctl --user is-active --quiet exo-surface.service'

guest_dir=$(ssh_vm 'mktemp -d /run/exo-atspi.XXXXXX')
[[ $guest_dir =~ ^/run/exo-atspi\.[A-Za-z0-9]{6}$ ]]
ssh_vm "chmod 0755 '$guest_dir'"
for file in atspi_vm_guard.py atspi_contract.py atspi_fixture.py atspi_inspect.py; do
  ssh_vm "cat > '$guest_dir/$file' && chown root:root '$guest_dir/$file' && chmod 0644 '$guest_dir/$file'" \
    < "$here/tools/$file"
done

as_exo 'systemctl --user stop exo-surface.service'
surface_stopped=1
probe_rc=0
probe_output=$(as_exo "timeout 120 env EXO_OS_VM_ACCEPTANCE=1 GDK_BACKEND=wayland GTK_A11Y=atspi \
  /opt/exo/bin/python '$guest_dir/atspi_inspect.py' --vm-acceptance \
  --fixture '$guest_dir/atspi_fixture.py' --image-source '$image_source' \
  --diagnostic-source '$diagnostic_source'" | head -c 131073) || probe_rc=$?
printf '%s\n' "$probe_output" > "$out/atspi-diagnostic.json"
# timeout normally signals the whole command group. If the fixture survived,
# terminate only this fixed child command in the disposable guest.
ssh_vm "pkill -TERM -u exo -f '^/opt/exo/bin/python $guest_dir/atspi_fixture.py --vm-acceptance\$' || test \$? = 1"
fixture_gone=0
for _ in 1 2 3 4 5; do
  if ! ssh_vm "pgrep -u exo -f '^/opt/exo/bin/python $guest_dir/atspi_fixture.py --vm-acceptance\$'" >/dev/null; then
    fixture_gone=1
    break
  fi
  sleep 0.2
done
restored=0
if as_exo 'systemctl --user start exo-surface.service && systemctl --user is-active --quiet exo-surface.service'; then
  restored=1
  surface_stopped=0
fi
validated=0
if printf '%s\n' "$probe_output" | "$here/.venv/bin/python" "$here/tools/validate-atspi-evidence.py" \
  --image-source "$image_source" --diagnostic-source "$diagnostic_source" --exit-code "$probe_rc"; then
  validated=1
fi
result atspi "$([[ $validated == 1 && $restored == 1 && $fixture_gone == 1 ]] && echo 1 || echo 0)" \
  "exact-PID synthetic tree, roles/text/focus; probe exit $probe_rc, fixture gone $fixture_gone, surface restored $restored; image ${image_source:0:7}, diagnostic ${diagnostic_source:0:7}"
accept_finish

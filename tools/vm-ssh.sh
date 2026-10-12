#!/usr/bin/env bash
# Run a command in the test VM started by tools/run-vm.sh (vmtest profile).
here="$(cd "$(dirname "$0")/.." && pwd)"
exec ssh -q -i "$here/image/out/vmtest-key" -p "${EXO_OS_SSH_PORT:-2222}" \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 \
  -o BatchMode=yes root@127.0.0.1 "$@"

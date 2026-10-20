#!/usr/bin/env bash
# Linux socket and GTK checks in a disposable container. No host mounts, credentials or desktop access.
# Requires Docker; optionally select an existing context with EXO_OS_DOCKER_CONTEXT.
# Uses only the exact core checkout at exo.pin, populated by tools/dev-env.sh.
#
# Extra arguments go to pytest. Unless you pass --basetemp, pytest and TMPDIR use private 0700 directories
# owned by the account that runs the tests. The container /tmp is a world-writable tmpfs, and the witness
# and model-spend path checks correctly refuse any ancestor that others can write, so tests that build
# root-controlled fixtures must not live under it. The checks themselves are not relaxed.
#
# EXO_OS_SANDBOX_TESTS=1 adds a second pass of tests/test_sandbox_linux.py as nobody in a privileged
# container. It runs and reports even when the main pass fails. Known limit: when the Docker host kernel
# sets kernel.apparmor_restrict_unprivileged_userns=1 (Colima's default Ubuntu VM), bubblewrap cannot
# configure loopback in a nested user namespace ("loopback: Failed RTM_NEWADDR: Operation not permitted")
# and 8 of the 9 sandbox tests fail. No container flag fixes this, and this script never changes host
# sysctls. Run that pass on a host or VM that permits unprivileged user namespaces.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
docker_cmd=(docker)
if [[ -n ${EXO_OS_DOCKER_CONTEXT:-} ]]; then docker_cmd+=(--context "$EXO_OS_DOCKER_CONTEXT"); fi
dep="$here/.deps/exo"
pin="$(tr -d '\n' < "$here/exo.pin")"
if [[ $(git -C "$dep" rev-parse HEAD) != "$pin" ]]; then
  echo 'The core checkout differs from exo.pin. Run tools/dev-env.sh first.' >&2
  exit 1
fi
"${docker_cmd[@]}" build -q -t exo-os-tests -f "$here/tools/test-linux.Dockerfile" "$here/tools"
run_args=(-d --cpus 2 --memory 2g --tmpfs /run --tmpfs /tmp:rw,exec)
if [[ ${EXO_OS_SANDBOX_TESTS:-0} == 1 ]]; then
  # Nested user/pid/network namespaces need a privileged disposable container. It has no host mounts,
  # credentials, or desktop socket. Never change host kernel sysctls to make these checks pass.
  run_args+=(--privileged)
fi
container=$("${docker_cmd[@]}" run "${run_args[@]}" exo-os-tests sleep infinity)
cleanup() { "${docker_cmd[@]}" rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${docker_cmd[@]}" exec "$container" mkdir -p /test/exo
git -C "$dep" archive HEAD | "${docker_cmd[@]}" exec -i "$container" tar -xf - -C /test/exo
# Explicit input list includes uncommitted work under review, excludes stores, keys, build output and .git.
COPYFILE_DISABLE=1 tar --no-xattrs --exclude=__pycache__ -C "$here" -cf - src tests image/mkosi.conf image/mkosi.extra image/mkosi.profiles image/mkosi.repart image/mkosi.postinst.chroot tools pyproject.toml exo.pin \
  | "${docker_cmd[@]}" exec -i "$container" tar -xf - -C /test/exo-os
"${docker_cmd[@]}" exec "$container" python -m pip install -q -e '/test/exo[tui,dev]' -e '/test/exo-os[dev]'
# Private fixture roots: root-owned 0700 for the main pass, nobody-owned 0700 for the sandbox pass.
"${docker_cmd[@]}" exec "$container" install -d -m 0700 /root/tmp
own_basetemp=1
for arg in "$@"; do
  case $arg in --basetemp|--basetemp=*) own_basetemp=0 ;; esac
done
main_args=("$@")
if [[ $own_basetemp == 1 ]]; then main_args+=(--basetemp=/root/pt); fi
status=0
"${docker_cmd[@]}" exec -e EXO_OS_TEST_CONTAINER=1 -e TMPDIR=/root/tmp "$container" python -m pytest -ra "${main_args[@]}" || status=$?
if [[ ${EXO_OS_SANDBOX_TESTS:-0} == 1 ]]; then
  # Clear supplementary groups and run as an ordinary account: the isolation must not rely on root.
  "${docker_cmd[@]}" exec "$container" install -d -m 0700 -o nobody -g nogroup /run/pt-nobody
  sandbox_status=0
  "${docker_cmd[@]}" exec --user root "$container" setpriv --reuid=nobody --regid=nogroup --clear-groups \
    env EXO_OS_SANDBOX_TESTS=1 TMPDIR=/run/pt-nobody python -m pytest -ra -p no:cacheprovider --basetemp=/run/pt-nobody/base \
    tests/test_sandbox_linux.py || sandbox_status=$?
  if [[ $sandbox_status != 0 ]]; then
    echo 'Sandbox pass failed. If the output shows "loopback: Failed RTM_NEWADDR", the Docker host kernel sets' >&2
    echo 'kernel.apparmor_restrict_unprivileged_userns=1 (an environment limit, not a test bug); see the script header.' >&2
    status=$sandbox_status
  fi
fi
exit "$status"

#!/usr/bin/env bash
# Linux socket and GTK checks in a disposable container. No host mounts, credentials or desktop access.
# Requires Docker; optionally select an existing context with EXO_OS_DOCKER_CONTEXT.
# Uses only the exact core checkout at exo.pin, populated by tools/dev-env.sh.
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
COPYFILE_DISABLE=1 tar --no-xattrs --exclude=__pycache__ -C "$here" -cf - src tests image/mkosi.conf image/mkosi.extra image/mkosi.postinst.chroot tools pyproject.toml exo.pin \
  | "${docker_cmd[@]}" exec -i "$container" tar -xf - -C /test/exo-os
"${docker_cmd[@]}" exec "$container" python -m pip install -q -e '/test/exo[tui,dev]' -e '/test/exo-os[dev]'
"${docker_cmd[@]}" exec -e EXO_OS_TEST_CONTAINER=1 "$container" python -m pytest -ra "$@"
if [[ ${EXO_OS_SANDBOX_TESTS:-0} == 1 ]]; then
  # Clear supplementary groups and run as an ordinary account: the isolation must not rely on root.
  "${docker_cmd[@]}" exec --user root "$container" setpriv --reuid=nobody --regid=nogroup --clear-groups \
    env EXO_OS_SANDBOX_TESTS=1 python -m pytest -ra tests/test_sandbox_linux.py
fi

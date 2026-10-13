#!/usr/bin/env python3
"""Small, destructive-only-in-its-own-tempdirs Linux tool-isolation probe for a throwaway VM."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pwd
import shlex
import sys
import tempfile
import time
import uuid
from pathlib import Path

CHECKS = (
    "workspace_read_write",
    "outside_secret_and_symlink",
    "network_socket_denied",
    "unix_socket_denied",
    "runtime_write_denied",
    "timeout_kills_grandchild",
    "cancel_kills_grandchild",
    "missing_bwrap_fails_closed",
)

# Software CPU emulation can spend several seconds loading the sandbox worker.
# The marker is still mandatory: a timeout before payload startup proves no cleanup.
TIMEOUT_PROBE_SECONDS = 10
TIMEOUT_DESCENDANT_DELAY_SECONDS = 15


def emit(checks: list[dict[str, str]]) -> None:
    print(json.dumps({"checks": checks}, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vm-acceptance",
        action="store_true",
        help="explicitly opt in to disposable-VM probes",
    )
    args = parser.parse_args()
    if not args.vm_acceptance or os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1":
        parser.error(
            "requires --vm-acceptance and EXO_OS_VM_ACCEPTANCE=1; run only inside the disposable VM"
        )
    if sys.platform != "linux" or not Path("/run/systemd/system").is_dir():
        parser.error("requires a booted Linux systemd VM")
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "exo":
        parser.error("run as the exo account, never as root")

    checks = []
    workspace_probe = outside_secret = None
    try:
        from dataclasses import replace

        from exo_os.sandbox.policy import Policy, SandboxError
        from exo_os.sandbox.runner import run

        policy = Policy.load()
        token = uuid.uuid4().hex
        workspace_probe = Path(
            tempfile.mkdtemp(prefix=f".isolation-{token}-", dir=policy.workspace)
        )
        outside_fd, outside_name = tempfile.mkstemp(
            prefix=f".isolation-secret-{token}-", dir=policy.workspace.parent
        )
        outside_secret = Path(outside_name)
        with os.fdopen(outside_fd, "w", encoding="utf-8") as stream:
            stream.write("SYNTHETIC_ISOLATION_CANARY_" + token)

        def check(name: str, fn) -> None:
            try:
                detail = fn()
                checks.append({"name": name, "status": "PASS", "detail": detail})
            except Exception as exc:  # noqa: BLE001 - every runtime failure must become a fixed FAIL result
                checks.append(
                    {
                        "name": name,
                        "status": "FAIL",
                        "detail": f"{type(exc).__name__}: {exc}"[:240],
                    }
                )

        def workspace_rw() -> str:
            rel = str(workspace_probe.relative_to(policy.workspace) / "allowed.txt")
            wrote = asyncio.run(
                run(
                    policy,
                    "write_file",
                    {"path": rel, "content": "workspace probe"},
                    str(policy.workspace),
                )
            )
            read = asyncio.run(
                run(policy, "read_file", {"path": rel}, str(policy.workspace))
            )
            assert (
                not wrote["is_error"]
                and not read["is_error"]
                and "workspace probe" in read["text"]
            ), "working-set file did not round-trip"
            return "a synthetic file inside the configured workspace round-tripped"

        def outside_secret_hidden() -> str:
            link = workspace_probe / "outside-link"
            link.symlink_to(outside_secret)
            result = asyncio.run(
                run(policy, "read_file", {"path": str(link)}, str(policy.workspace))
            )
            assert "SYNTHETIC_ISOLATION_CANARY_" not in result["text"], (
                "outside secret was readable through a workspace symlink"
            )
            return "synthetic secret outside the workspace was not disclosed through a symlink"

        def shell(command: str, selected=policy):
            return asyncio.run(
                run(selected, "bash", {"command": command}, str(policy.workspace))
            )

        def denied_socket(family: str) -> str:
            code = "import socket; socket.socket(" + family + ")"
            result = shell("python3 -c " + shlex.quote(code))
            assert "operation not permitted" in result["text"].lower(), (
                "socket creation was not denied"
            )
            return "socket creation returned EPERM inside the isolated tool"

        runtime_path = f"/usr/.exo-isolation-{uuid.uuid4().hex}"

        def runtime_readonly() -> str:
            result = shell("touch " + shlex.quote(runtime_path))
            assert (
                "read-only" in result["text"].lower()
                or "permission denied" in result["text"].lower()
            ), "runtime write was not refused"
            return "root-owned runtime rejected a synthetic write"

        def timeout_kills() -> str:
            started = workspace_probe / "timeout-started"
            survived = workspace_probe / "timeout-survived"
            command = (
                f"touch {shlex.quote(str(started))}; setsid sh -c "
                + shlex.quote(f"sleep {TIMEOUT_DESCENDANT_DELAY_SECONDS}; touch {shlex.quote(str(survived))}")
                + " & sleep 30"
            )
            try:
                shell(command, replace(policy, timeout=TIMEOUT_PROBE_SECONDS))
            except SandboxError as exc:
                assert "time limit" in str(exc), (
                    "execution failed for a reason other than timeout"
                )
            else:
                raise AssertionError("timeout did not stop the tool")
            time.sleep(TIMEOUT_DESCENDANT_DELAY_SECONDS + 0.4)
            assert started.exists(), (
                "timeout expired before payload startup; descendant cleanup was not exercised"
            )
            assert not survived.exists(), (
                "detached grandchild survived timeout"
            )
            return "timeout killed a detached grandchild before its delayed write"

        def cancel_kills() -> str:
            async def exercise() -> None:
                started = workspace_probe / "cancel-started"
                survived = workspace_probe / "cancel-survived"
                command = (
                    f"touch {shlex.quote(str(started))}; setsid sh -c "
                    + shlex.quote(f"sleep 2; touch {shlex.quote(str(survived))}")
                    + " & sleep 30"
                )
                task = asyncio.create_task(
                    run(policy, "bash", {"command": command}, str(policy.workspace))
                )
                for _ in range(100):
                    if started.exists():
                        break
                    if task.done():
                        await task
                        raise AssertionError(
                            "tool ended before cancellation probe started"
                        )
                    await asyncio.sleep(0.05)
                assert started.exists(), "tool did not start"
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("cancellation did not propagate")
                await asyncio.sleep(2.4)
                assert not survived.exists(), (
                    "detached grandchild survived cancellation"
                )

            asyncio.run(exercise())
            return "cancellation killed a detached grandchild before its delayed write"

        def missing_bwrap() -> str:
            target = workspace_probe / "must-not-run"
            missing = replace(policy, bwrap=str(workspace_probe / "no-such-bwrap"))
            try:
                shell("touch " + shlex.quote(str(target)), missing)
            except OSError:
                pass
            else:
                raise AssertionError("missing bubblewrap did not refuse execution")
            assert not target.exists(), "payload ran after bubblewrap startup failed"
            return "missing bubblewrap refused before the tool payload ran"

        operations = (
            ("workspace_read_write", workspace_rw),
            ("outside_secret_and_symlink", outside_secret_hidden),
            ("network_socket_denied", lambda: denied_socket("")),
            ("unix_socket_denied", lambda: denied_socket("socket.AF_UNIX")),
            ("runtime_write_denied", runtime_readonly),
            ("timeout_kills_grandchild", timeout_kills),
            ("cancel_kills_grandchild", cancel_kills),
            ("missing_bwrap_fails_closed", missing_bwrap),
        )
        for name, operation in operations:
            check(name, operation)
    except Exception as exc:  # noqa: BLE001 - setup failure still emits all eight fixed FAIL results
        # Keep a fixed result shape if the installed policy/runtime cannot even load.
        remaining = [
            name for name in CHECKS if name not in {item["name"] for item in checks}
        ]
        for name in remaining:
            checks.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "detail": f"setup refused: {type(exc).__name__}",
                }
            )
    finally:
        if workspace_probe is not None:
            import shutil

            shutil.rmtree(workspace_probe, ignore_errors=True)
        if outside_secret is not None:
            outside_secret.unlink(missing_ok=True)

    # A malformed or truncated probe can never report success.
    if tuple(item["name"] for item in checks) != CHECKS:
        checks = [
            {"name": name, "status": "FAIL", "detail": "probe result set/order invalid"}
            for name in CHECKS
        ]
    emit(checks)
    return 0 if all(item["status"] == "PASS" for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

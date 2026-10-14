"""Real kernel enforcement; enabled only inside our disposable Linux test container."""
import asyncio
import os
from pathlib import Path
import sys

import pytest

from exo_os.sandbox.policy import Policy, SandboxError
from exo_os.sandbox.runner import run

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("EXO_OS_SANDBOX_TESTS") != "1",
    reason="requires disposable Linux namespace test container",
)


@pytest.fixture
def policy(tmp_path):
    import exo
    import exo_os

    work = tmp_path / "work"
    work.mkdir()
    return Policy(work, runtime=(Path("/usr"), Path(sys.prefix), Path(exo.__file__).parents[1],
                                Path(exo_os.__file__).parents[1]), python=sys.executable)


def shell(policy, command):
    return asyncio.run(run(policy, "bash", {"command": command}, str(policy.workspace)))


def test_real_file_tools_write_and_read_working_set(policy):
    out = asyncio.run(run(policy, "write_file", {"path": "hello", "content": "isolated"}, str(policy.workspace)))
    assert not out["is_error"], out
    out = asyncio.run(run(policy, "read_file", {"path": "hello"}, str(policy.workspace)))
    assert not out["is_error"] and "isolated" in out["text"], out


def test_real_namespace_hides_secrets_and_root_socket(policy, tmp_path, monkeypatch):
    secret = tmp_path / "credential"
    secret.write_text("TEST_SECRET_SHOULD_NOT_APPEAR")
    (policy.workspace / "escape").symlink_to(secret)
    monkeypatch.setenv("EXO_TEST_SECRET", "TEST_SECRET_SHOULD_NOT_APPEAR")
    out = shell(policy, "cat escape; cat /proc/1/root" + str(secret) + "; env; ls /run")
    assert "TEST_SECRET_SHOULD_NOT_APPEAR" not in out["text"]
    assert "exo-sysd.sock" not in out["text"]
    assert "No such file" in out["text"] or "Permission denied" in out["text"], out


def test_real_network_socket_creation_is_denied(policy):
    out = shell(policy, "python3 -c 'import socket; socket.socket()'")
    assert "Operation not permitted" in out["text"], out


def test_real_unix_socket_creation_is_denied(policy):
    out = shell(policy, "python3 -c 'import socket; socket.socket(socket.AF_UNIX)' ")
    assert "Operation not permitted" in out["text"], out


def test_runtime_cannot_be_modified(policy):
    out = shell(policy, "touch /usr/exo-sandbox-write-probe")
    assert "Read-only" in out["text"] or "Permission denied" in out["text"], out


def test_namespace_failure_cannot_run_payload(policy, tmp_path):
    from dataclasses import replace

    with pytest.raises(OSError):
        shell(replace(policy, bwrap="/does-not-exist"), "touch escaped")
    assert not (policy.workspace / "escaped").exists()


def test_timeout_kills_detached_children(policy):
    from dataclasses import replace
    import time

    with pytest.raises(SandboxError, match="time limit"):
        shell(replace(policy, timeout=2), "setsid sh -c 'sleep 4; touch survived' & sleep 30")
    time.sleep(3)
    assert not (policy.workspace / "survived").exists()


def test_cancel_kills_detached_children(policy):
    async def exercise():
        task = asyncio.create_task(run(policy, "bash", {
            "command": "touch started; setsid sh -c 'sleep 3; touch survived-cancel' & sleep 30",
        }, str(policy.workspace)))
        for _ in range(100):
            if (policy.workspace / "started").exists():
                break
            if task.done():
                pytest.fail(str(await task))
            await asyncio.sleep(0.1)
        assert (policy.workspace / "started").exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(4)
        assert not (policy.workspace / "survived-cancel").exists()

    asyncio.run(exercise())


def test_inheritable_host_descriptor_is_not_passed(policy, tmp_path):
    secret = tmp_path / "private-fd"
    secret.write_text("HOST_FD_CANARY")
    fd = os.open(secret, os.O_RDONLY)
    try:
        os.set_inheritable(fd, True)
        out = shell(policy, f"python3 -c 'import os; print(os.read({fd}, 100))'")
        assert "HOST_FD_CANARY" not in out["text"]
    finally:
        os.close(fd)

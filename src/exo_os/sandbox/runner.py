"""One subprocess tree per call, bounded and reaped on cancellation or timeout."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

from .policy import Policy, SandboxError

SANDBOX_TOOLS = frozenset({"read_file", "write_file", "edit_file", "apply_patch", "glob", "grep", "code_find", "bash"})


def command(policy: Policy, cwd: str) -> list[str]:
    argv = [policy.bwrap, "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-net",
            "--unshare-uts", "--die-with-parent", "--new-session", "--disable-userns",
            "--cap-drop", "ALL", "--clearenv"]
    for path in policy.runtime:
        argv += ["--ro-bind", str(path), str(path)]
    for path in ("bin", "sbin", "lib", "lib64"):
        if Path(f"/usr/{path}").exists():
            argv += ["--symlink", f"usr/{path}", f"/{path}"]
    # No host /etc, /run, home, store, credentials, compositor or agent sockets.
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
             "--dir", "/etc", "--dir", "/run", "--bind", str(policy.workspace), str(policy.workspace),
             "--setenv", "HOME", "/tmp/home", "--setenv", "PATH", "/usr/bin:/bin",
             "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
             "--chdir", cwd, "--", policy.python, "-I", "-m", "exo_os.sandbox.worker"]
    return argv


async def run(policy: Policy, tool: str, args: dict, cwd: str) -> dict:
    if tool not in SANDBOX_TOOLS or (tool == "bash" and args.get("background")):
        raise SandboxError("this execution path is not supported by the sandbox")
    cwd = policy.cwd(cwd)
    if tool == "bash" and args.get("cwd"):
        policy.cwd(str(Path(cwd) / args["cwd"]))
    payload = json.dumps({"tool": tool, "args": args, "cwd": cwd,
                          "readonly": [str(p) for p in policy.runtime]}, allow_nan=False).encode()
    if len(payload) > 128 * 1024:
        raise SandboxError("tool request exceeds the sandbox limit")
    proc = await asyncio.create_subprocess_exec(
        *command(policy, cwd), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        start_new_session=True, close_fds=True)

    async def collect(stream):
        data = bytearray()
        while part := await stream.read(16384):
            data.extend(part)
            if len(data) > policy.max_output:
                raise SandboxError("tool output exceeded its limit; execution stopped, changes may be partial")
        return bytes(data)

    async def exchange():
        proc.stdin.write(payload)
        await proc.stdin.drain()
        proc.stdin.close()
        stdout, stderr = await asyncio.gather(collect(proc.stdout), collect(proc.stderr))
        await proc.wait()
        if proc.returncode:
            raise SandboxError("sandbox refused or failed; changes may be partial: " + stderr.decode(errors="replace")[-1000:])
        try:
            result = json.loads(stdout)
            if set(result) != {"text", "is_error"} or not isinstance(result["text"], str) or type(result["is_error"]) is not bool:
                raise ValueError("invalid result")
            return result
        except (ValueError, TypeError) as exc:
            raise SandboxError("sandbox returned no valid execution result; do not automatically retry") from exc

    try:
        return await asyncio.wait_for(exchange(), policy.timeout)
    except asyncio.TimeoutError as exc:
        raise SandboxError("tool time limit reached; execution stopped, changes may be partial") from exc
    finally:
        # Killing the namespace's init also kills grandchildren, including setsid/double-fork jobs.
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await proc.wait()

#!/usr/bin/env python3
"""Record bounded compositor observations; invalid IPC is never a zero window count."""
from __future__ import annotations

import argparse
import json
import os
import signal
import selectors
import subprocess
import sys
import time
from pathlib import Path

LIMIT = 32768
COMMAND = "cd /tmp && as-exo hyprctl clients -j"


def foot_count(raw: str) -> int:
    clients = json.loads(raw)
    if not isinstance(clients, list) or any(not isinstance(c, dict) or not isinstance(c.get("class"), str)
                                           for c in clients):
        raise ValueError("hyprctl clients must be a JSON list of clients with string classes")
    return sum(client["class"] == "foot" for client in clients)


def observe(command: list[str], timeout: float) -> dict:
    started = time.monotonic()
    deadline = started + timeout
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out, oversized = False, False
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                selector.register(getattr(process, name), selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(remaining):
                    buffer = buffers[key.data]
                    chunk = os.read(key.fileobj.fileno(), min(4096, LIMIT + 1 - len(buffer)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffer.extend(chunk)
                    if len(buffer) > LIMIT:
                        oversized = True
                        break
                if oversized:
                    break
        if not timed_out and not oversized:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        if timed_out or oversized or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=1)  # bounded reap after killing the local SSH process group
        process.stdout.close()
        process.stderr.close()
    out, err = bytes(buffers["stdout"]), bytes(buffers["stderr"])
    row = {"started_monotonic": started, "observed_monotonic": time.monotonic(),
           "exit_code": process.returncode, "timed_out": timed_out,
           "stdout": out[:LIMIT].decode("utf-8", errors="replace"),
           "stderr": err[:LIMIT].decode("utf-8", errors="replace"),
           "stdout_truncated": len(out) > LIMIT, "stderr_truncated": len(err) > LIMIT,
           "valid": False, "foot_count": None}
    if oversized:
        row["error"] = "IPC stdout or stderr exceeded the streaming byte cap"
    elif process.returncode != 0 or timed_out:
        row["error"] = "command failed or timed out"
    else:
        try:
            row["foot_count"] = foot_count(out.decode("utf-8"))
            row["valid"] = True
        except (ValueError, UnicodeError) as exc:
            row["error"] = str(exc)
    return row


def poll(command: list[str], *, timeout: float, before: int | None, interval: float = 0.5,
         command_timeout: float = 5.0) -> dict:
    started = time.monotonic()
    deadline = started + timeout
    observations = []
    status, count = "FAIL", None
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            row = observe(command, min(command_timeout, remaining))
        except (OSError, subprocess.SubprocessError) as exc:
            row = {"started_monotonic": time.monotonic(), "observed_monotonic": time.monotonic(),
                   "exit_code": None, "timed_out": False, "stdout": "", "stderr": "",
                   "stdout_truncated": False, "stderr_truncated": False,
                   "valid": False, "foot_count": None, "error": str(exc)}
        row["elapsed_seconds"] = row["observed_monotonic"] - started
        observations.append(row)
        if (row["valid"] and row["observed_monotonic"] <= deadline
                and (before is None or row["foot_count"] > before)):
            status, count = "PASS", row["foot_count"]
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    return {"probe": "phase3-foot-readiness-v1", "status": status,
            "mode": "baseline" if before is None else "increase", "before": before, "foot_count": count,
            "started_monotonic": started, "deadline_monotonic": deadline,
            "finished_monotonic": time.monotonic(), "observations": observations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60, choices=range(1, 121), metavar="1..120")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--baseline", action="store_true")
    modes.add_argument("--after", type=int)
    args = parser.parse_args()
    if args.after is not None and args.after < 0:
        parser.error("baseline count must be nonnegative")
    result = poll([str(args.ssh), COMMAND], timeout=args.timeout, before=args.after)
    args.record.write_text(json.dumps(result, indent=2) + "\n")
    if result["status"] == "PASS":
        print(result["foot_count"])
        return 0
    print(f"no valid {'baseline' if args.baseline else 'foot-count increase'} before deadline; see {args.record}",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

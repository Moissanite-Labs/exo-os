#!/usr/bin/env python3
"""Wait for an acceptance VM over SSH without exceeding a monotonic wall-clock deadline."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: accept-wait-ssh.py SECONDS EXPECTED-OUTPUT COMMAND...", file=sys.stderr)
        return 2
    try:
        timeout = int(sys.argv[1])
    except ValueError:
        return 2
    if timeout < 1 or timeout > 900:
        return 2

    expected = sys.argv[2]
    command = sys.argv[3:]
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 1
        process = subprocess.Popen(
            ["bash", "-c", 'ssh_vm "$@"', "accept-wait-ssh", *command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(timeout=min(5.0, remaining))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            stdout = ""
            status = 124
        else:
            status = process.returncode

        output = stdout.strip()
        if status == 0 and (expected == "-" or (output and output != expected)):
            if output:
                sys.stdout.write(stdout)
            return 0

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 1
        time.sleep(min(1.0, remaining))


if __name__ == "__main__":
    raise SystemExit(main())

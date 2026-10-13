#!/usr/bin/env python3
"""Verify the disposable VM surface's declared and effective renderer without changing it."""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
from pathlib import Path

SERVICE = "exo-surface.service"
RENDERER = "cairo"
LIMIT = 16384
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def declared_renderer(raw: str) -> str:
    if len(raw.encode()) > LIMIT or any(ord(char) < 32 or ord(char) == 127 for char in raw.removesuffix("\n")):
        raise ValueError("service Environment is oversized or contains control characters")
    found = []
    for item in shlex.split(raw, posix=True):
        key, sep, value = item.partition("=")
        if not sep or not KEY.fullmatch(key):
            raise ValueError("service Environment contains a malformed assignment")
        if key == "GSK_RENDERER":
            found.append(value)
    if found != [RENDERER]:
        raise ValueError("vmtest surface requires exactly one declared GSK_RENDERER=cairo")
    return RENDERER


def effective_renderer(raw: bytes) -> str:
    if len(raw) > LIMIT or not raw.endswith(b"\0"):
        raise ValueError("surface process environment is oversized or malformed")
    found = [item.partition(b"=")[2] for item in raw.split(b"\0") if item.startswith(b"GSK_RENDERER=")]
    if found != [RENDERER.encode()]:
        raise ValueError("surface process GSK_RENDERER differs from its vmtest declaration")
    return RENDERER


def validate_record(data: object) -> str:
    if (not isinstance(data, dict) or set(data) != {"service", "main_pid", "declared", "effective"}
            or data["service"] != SERVICE or type(data["main_pid"]) is not int or data["main_pid"] <= 1
            or data["declared"] != RENDERER or data["effective"] != RENDERER):
        raise ValueError("surface renderer record is missing or mismatched")
    return RENDERER


def inspect_service() -> dict:
    if (os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1" or sys.platform != "linux"
            or not Path("/run/systemd/system").is_dir() or os.geteuid() == 0
            or pwd.getpwuid(os.geteuid()).pw_name != "exo"):
        raise ValueError("requires the authenticated disposable systemd VM as exo")

    def property_value(name: str) -> str:
        reply = subprocess.run(["systemctl", "--user", "show", SERVICE, "-p", name, "--value"],
                               capture_output=True, text=True, check=True, timeout=10)
        if len(reply.stdout.encode()) > LIMIT:
            raise ValueError("surface service property exceeds the input bound")
        return reply.stdout

    declared = declared_renderer(property_value("Environment"))
    pid = property_value("MainPID").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,9}", pid) or int(pid) <= 1:
        raise ValueError("surface service has no valid running main PID")
    with Path(f"/proc/{pid}/environ").open("rb") as stream:
        effective = effective_renderer(stream.read(LIMIT + 1))
    if property_value("MainPID").strip() != pid:
        raise ValueError("surface service changed process during renderer inspection")
    return {"service": SERVICE, "main_pid": int(pid), "declared": declared, "effective": effective}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--vm-acceptance", action="store_true")
    modes.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    try:
        if args.validate:
            raw = sys.stdin.read(LIMIT + 1)
            if len(raw.encode()) > LIMIT:
                raise ValueError("surface renderer record exceeds the input bound")
            print(validate_record(json.loads(raw)))
        else:
            print(json.dumps(inspect_service(), sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"surface renderer: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Audit an exact synthetic fixture argv, or validate the remote audit result."""

from __future__ import annotations

import argparse
import os
import pwd
import sys
from pathlib import Path

EXPECTED_PYTHON = b"/opt/exo/bin/python"


def matching_pids(proc: Path, fixture: Path, uid: int) -> list[int]:
    expected = (EXPECTED_PYTHON, os.fsencode(fixture), b"--vm-acceptance")
    matches = []
    for child in proc.iterdir():
        if not child.name.isdecimal():
            continue
        try:
            if child.stat(follow_symlinks=False).st_uid != uid:
                continue
            argv = tuple(child.joinpath("cmdline").read_bytes().rstrip(b"\0").split(b"\0"))
        except FileNotFoundError:  # process exited during the scan
            continue
        if argv == expected:
            matches.append(int(child.name))
    return matches


def confirmed_gone(output: str, exit_code: int) -> bool:
    return exit_code == 0 and output == "gone\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--guest-check", action="store_true")
    group.add_argument("--validate", action="store_true")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--exit-code", type=int)
    args = parser.parse_args()
    if args.validate:
        if args.exit_code is None or not confirmed_gone(sys.stdin.read(16), args.exit_code):
            return 1
        return 0
    if (args.fixture is None or args.exit_code is not None or sys.platform != "linux"
            or os.geteuid() != 0 or os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1"
            or not Path("/run/systemd/system").is_dir()):
        parser.error("guest check requires the authenticated disposable VM as root")
    if (args.fixture.parent.parent != Path("/run")
            or not args.fixture.parent.name.startswith("exo-atspi.")):
        parser.error("fixture must be in the fixed root-owned /run diagnostic directory")
    uid = pwd.getpwnam("exo").pw_uid
    if uid == 0:
        parser.error("exo must be non-root")
    found = matching_pids(Path("/proc"), args.fixture, uid)
    print("remaining" if found else "gone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

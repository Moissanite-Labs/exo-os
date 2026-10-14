#!/usr/bin/env python3
"""Select the single documented mkosi profile for the Exo OS build wrapper.

Only the selected profile is emitted; build arguments are never serialized into
the installed manifest (they may contain paths or credentials).
"""

from __future__ import annotations

import sys


def selected_profile(args: list[str]) -> str:
    selected = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--profile":
            index += 1
            if index >= len(args):
                raise ValueError("--profile requires the vmtest value")
            value = args[index]
        elif arg.startswith("--profile="):
            value = arg.removeprefix("--profile=")
        elif (arg in ("-C", "-I", "--directory", "--include")
              or arg.startswith(("-C", "-I", "--directory=", "--include="))):
            raise ValueError("config-root and include overrides are unsupported; use the tracked image tree")
        elif arg == "--" or arg.startswith("--prof"):
            raise ValueError(f"ambiguous or unsupported profile selection: {arg}")
        else:
            index += 1
            continue
        if selected is not None:
            raise ValueError("build accepts one --profile selection only")
        if value != "vmtest":
            raise ValueError("only --profile=vmtest is supported; omit it for production")
        selected = value
        index += 1
    return selected or "production"


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    try:
        print(selected_profile(args))
    except ValueError as exc:
        print(f"build profile: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

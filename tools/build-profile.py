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
        elif arg.split("=", 1)[0] in (
                "--tools-tree", "--tools-tree-distribution", "--tools-tree-release",
                "--tools-tree-profile"):
            if "=" in arg:
                option_value = arg.split("=", 1)[1]
            else:
                index += 1
                if index >= len(args):
                    raise ValueError(f"{arg} requires a value")
                option_value = args[index]
            if not option_value or option_value.startswith("-"):
                raise ValueError(f"{arg.split('=', 1)[0]} requires a value")
            index += 1
            continue
        else:
            raise ValueError(
                f"unsupported build option: {arg}; config-root and include overrides are unsupported")
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

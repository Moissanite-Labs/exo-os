#!/usr/bin/env python3
"""Accept only the complete synthetic GTK probe result expected by phase 3."""
import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("broadway-private", "wayland-layer"), required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    if args.exit_code != 0:
        return 1
    try:
        actual = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return 1
    expected = {"probe": "exo-surface-gtk-v1", "backend": args.backend,
                "layered": args.backend == "wayland-layer", "status": "PASS"}
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail closed on the bounded AT-SPI diagnostic's JSON and process exit status."""

from __future__ import annotations

import argparse
import sys

from atspi_contract import parse_and_validate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-source", required=True)
    parser.add_argument("--diagnostic-source", required=True)
    parser.add_argument("--exit-code", required=True, type=int)
    args = parser.parse_args()
    raw = sys.stdin.read(128 * 1024 + 1)
    if parse_and_validate(raw, image_source=args.image_source,
                          diagnostic_source=args.diagnostic_source,
                          exit_code=args.exit_code):
        return 0
    print("AT-SPI evidence is missing, malformed, or fails a required stage", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

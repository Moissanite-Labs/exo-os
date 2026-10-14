#!/usr/bin/env python3
"""Return success only when every Exo model role resolves to RuleFake."""

from __future__ import annotations

import sys


EXPECTED = {"main", "aux", "decision", "eval", "fast", "standard", "deep"}


def valid_roles(text: str) -> bool:
    rows = [line.split() for line in text.splitlines() if line.strip()]
    return (
        len(rows) == 7
        and {row[0] for row in rows if len(row) == 2} == EXPECTED
        and all(len(row) == 2 and row[1] == "fake:scripted" for row in rows)
    )


if __name__ == "__main__":
    raise SystemExit(0 if valid_roles(sys.stdin.read()) else 1)

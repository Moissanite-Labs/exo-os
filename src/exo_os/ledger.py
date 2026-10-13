"""Where Exo OS writes its events: the principal's signed Exo ledger.

Exo's ledger accepts a closed set of kinds, so Exo OS follows the gate's convention: kind "action",
with `body.os = "exo-os"` and `body.phase` naming the step (change.proposed, change.applied,
change.reverted, ...). `exo log verify` checks these rows like any other.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

AUTHOR = "exo-os"


class Sink(Protocol):
    def append(self, phase: str, body: dict[str, Any]) -> str | None: ...


class NullSink:
    def append(self, phase: str, body: dict[str, Any]) -> str | None:
        return None


class MemorySink:
    """Keeps events in a list. For tests and dry runs."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, phase: str, body: dict[str, Any]) -> str | None:
        self.events.append({"phase": phase, **body})
        return f"mem-{len(self.events)}"


class ExoLedgerSink:
    """Appends to the principal ledger through the configured signing backend."""

    def __init__(self, store: Path, key_path: Path) -> None:
        from exo.ledger_authority.factory import open_writable_ledger
        # Backend selection must precede all local key reads or generation.
        self.ledger = open_writable_ledger(store / "ledger.sqlite", AUTHOR, key_path,
                                           writer=AUTHOR, auto_keygen=True)

    def append(self, phase: str, body: dict[str, Any]) -> str | None:
        ev = self.ledger.append("action", {"os": "exo-os", "phase": phase, **body}, author=AUTHOR)
        return ev["id"]


def open_sink(store: Path | None, exo_home: Path | None) -> Sink:
    """The principal's ledger when a store exists, else nothing (changes still work and are still in git)."""
    if store is None or not store.is_dir():
        from exo.ledger_authority import AuthorityUnavailable
        from exo.ledger_authority.factory import configured
        if configured():
            raise AuthorityUnavailable("protected authority requires an initialized bound store")
        return NullSink()
    home = exo_home or Path(os.environ.get("EXO_HOME", Path.home() / ".exo"))
    return ExoLedgerSink(store, home / "keys" / f"{AUTHOR}.key")

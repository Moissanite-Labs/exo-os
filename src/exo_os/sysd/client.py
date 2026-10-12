"""Ask exo-sysd to do one named action, and record its signed receipt in the principal's ledger."""
from __future__ import annotations

import json
import os
import socket
from typing import Any

from .. import paths
from ..ledger import open_sink


class SysdError(Exception):
    pass


class SysdClient:
    def __init__(self, path: str | None = None, timeout: float = 1800) -> None:
        self.path = path or os.environ.get("EXO_SYSD_SOCKET", "/run/exo-sysd.sock")
        self.timeout = timeout

    def call(self, action: str, args: dict[str, Any] | None = None, *, record: bool = True) -> Any:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.path)
                s.sendall(json.dumps({"action": action, "args": args or {}}).encode() + b"\n")
                reply = json.loads(s.makefile("rb").readline() or b"{}")
        except (OSError, ValueError) as e:
            raise SysdError(f"The system helper is not reachable ({e}).") from None
        if record and reply.get("receipt") and action not in ("status", "snapshot.list"):
            open_sink(paths.store(), paths.exo_home()).append("system.action", {"receipt": reply["receipt"]})
        if not reply.get("ok"):
            raise SysdError(reply.get("error") or "the system helper refused")
        return reply.get("result")

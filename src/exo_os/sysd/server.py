"""exo-sysd server: one JSON request per line in, one JSON reply per line out, over a Unix socket.

The socket comes from systemd (exo-sysd.socket: /run/exo-sysd.sock, mode 0660, group exo), so only
members of the exo group can connect; the peer's uid is also read with SO_PEERCRED and written into
every receipt. Actions run one at a time. Each reply carries a receipt signed with exo-sysd's own key,
which the caller copies into the principal's ledger: the record of a root action is signed by the
process that did it, not by the agent that asked (DESIGN.md §5.4, separate signer).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .actions import ActionError, Actions

KEY = Path("/var/lib/exo-sysd/sysd.key")
LOG = Path("/var/log/exo-sysd.log")
MAX_REQUEST = 64 * 1024


class Server:
    def __init__(self, actions: Actions, key_path: Path = KEY, log_path: Path | None = LOG,
                 allowed_gid: int | None = None) -> None:
        self.actions = actions
        self.table = actions.table()
        self.lock = threading.Lock()
        self.log_path = log_path
        self.allowed_gid = allowed_gid
        self.key = _load_or_make_key(key_path)

    def handle_line(self, line: bytes, peer: tuple[int, int, int]) -> dict[str, Any]:
        pid, uid, gid = peer
        try:
            req = json.loads(line)
            action, args = req["action"], req.get("args", {})
            if not isinstance(args, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "error": "bad request"}
        fn = self.table.get(action)
        if fn is None:
            return {"ok": False, "error": f"exo-sysd does not do {action!r}; it does: {', '.join(self.table)}"}
        started = time.time()
        with self.lock:
            try:
                result, ok, err = fn(args), True, None
            except ActionError as e:
                result, ok, err = None, False, str(e)
            except Exception as e:  # never take the helper down; report it
                result, ok, err = None, False, f"internal error: {type(e).__name__}: {e}"
        receipt = self._receipt(action, args, ok, result, err, uid, pid, started)
        return {"ok": ok, "result": result, "error": err, "receipt": receipt}

    def _receipt(self, action: str, args: dict, ok: bool, result: Any, err: str | None, uid: int, pid: int,
                 started: float) -> dict[str, Any]:
        from exo import crypto

        doc = {"by": "exo-sysd", "action": action, "args": args, "ok": ok, "result": result, "error": err,
               "uid": uid, "pid": pid, "ts": crypto.now_ts(), "ms": int((time.time() - started) * 1000),
               "pub": crypto.pubkey_b64(self.key)}
        doc = crypto.sign_doc(doc, self.key)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(doc, sort_keys=True) + "\n")
        return doc

    def serve(self, sock: socket.socket) -> None:
        while True:
            conn, _ = sock.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        with conn:
            pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                  struct.calcsize("3i")))
            if not self._allowed(uid, gid):
                conn.sendall(b'{"ok": false, "error": "not allowed"}\n')
                return
            f = conn.makefile("rb")
            line = f.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST:
                conn.sendall(b'{"ok": false, "error": "request too large"}\n')
                return
            reply = self.handle_line(line, (pid, uid, gid))
            conn.sendall(json.dumps(reply).encode() + b"\n")

    def _allowed(self, uid: int, gid: int) -> bool:
        if uid == 0 or self.allowed_gid is None:
            return True
        import pwd

        try:
            groups = os.getgrouplist(pwd.getpwuid(uid).pw_name, gid)
        except KeyError:
            return False
        return self.allowed_gid in groups


def _load_or_make_key(path: Path):
    from exo import crypto

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        crypto.save_key(crypto.keygen(), path)
    return crypto.load_key(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="exo-sysd")
    ap.add_argument("--socket", default="/run/exo-sysd.sock", help="used only without systemd socket activation")
    ap.add_argument("--group", default="exo", help="only members of this group may connect")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("exo-sysd must run as root (it is the only part of Exo OS that does)", file=sys.stderr)
        return 1
    import grp

    try:
        gid = grp.getgrnam(a.group).gr_gid
    except KeyError:
        print(f"group {a.group} does not exist", file=sys.stderr)
        return 1
    if os.environ.get("LISTEN_FDS") == "1" and os.environ.get("LISTEN_PID") == str(os.getpid()):
        sock = socket.socket(fileno=3)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(a.socket):
            os.unlink(a.socket)
        sock.bind(a.socket)
        os.chown(a.socket, 0, gid)
        os.chmod(a.socket, 0o660)
        sock.listen(8)
    Server(Actions(), allowed_gid=gid).serve(sock)
    return 0


if __name__ == "__main__":
    sys.exit(main())

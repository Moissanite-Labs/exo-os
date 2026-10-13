"""Socket-activated exo-egress service. The desktop UID is trusted, tools cannot reach it."""
from __future__ import annotations

import os
import socket
import sqlite3
import stat
import threading
from pathlib import Path

from ..sandbox.policy import trusted_path
from ..sysd.protocol import decode, encode, peer_credentials, read_line
from .broker import Broker, Journal
from .policy import (
    MAX_WIRE,
    TIMEOUT,
    WORKERS,
    EgressError,
    load_policy,
    validate_request,
)


def account_uid(name: str) -> int:
    path = trusted_path(Path("/etc/passwd"))
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise EgressError("invalid account database")
    matches = [line.split(":") for line in path.read_text().splitlines() if line.split(":", 1)[0] == name]
    if len(matches) != 1 or len(matches[0]) != 7 or not matches[0][2].isdigit():
        raise EgressError("missing static egress account")
    return int(matches[0][2])


class Server:
    def __init__(self, broker: Broker, desktop_uid: int):
        self.broker, self.desktop_uid = broker, desktop_uid

    def serve_connection(self, conn):
        with conn:
            identifiers = {}
            try:
                if peer_credentials(conn)[1] != self.desktop_uid:
                    return
                conn.settimeout(TIMEOUT)
                # Client SO_PASSCRED supplies the sending process's UID to recvmsg.
                conn.sendall(b"exo-egress-v1\n")
                raw = validate_request(decode(read_line(conn, MAX_WIRE, TIMEOUT)))
                identifiers = {"nonce": raw["nonce"], "request_id": raw["request_id"]}
                reply = self.broker.handle(raw)
            except (EgressError, ValueError, OSError, sqlite3.Error):
                reply = {**identifiers, "version": 1, "state": "refused", "retryable": False,
                         "message": "Invalid or unavailable egress request"}
            try:
                conn.sendall(encode(reply))
            except OSError:
                pass  # durable reservation/outcome survives loss of client reply

    def serve(self, listener):
        slots = threading.BoundedSemaphore(WORKERS)
        def worker(conn):
            try:
                self.serve_connection(conn)
            finally:
                slots.release()
        while True:
            slots.acquire()
            try:
                conn, _ = listener.accept()
            except BaseException:
                slots.release()
                raise
            threading.Thread(target=worker, args=(conn,), daemon=True).start()


def main():
    service_uid = account_uid("exo-egress")
    if os.geteuid() != service_uid or service_uid == 0:
        raise EgressError("egress must run as static exo-egress user")
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise EgressError("egress requires its systemd socket")
    profiles = load_policy()
    state = Path("/var/lib/exo-egress")
    trusted_path(state.parent, directory=True)
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != service_uid or info.st_mode & 0o077:
        raise EgressError("invalid egress state directory")
    for leaf in state.iterdir():
        item = leaf.lstat()
        if not stat.S_ISREG(item.st_mode) or item.st_uid != service_uid or item.st_mode & 0o077:
            raise EgressError("invalid egress state file")
    credential_dir = Path(os.environ.get("CREDENTIALS_DIRECTORY", "/run/credentials/exo-egress.service"))
    if not credential_dir.is_absolute():
        raise EgressError("credential directory must be absolute")
    broker = Broker(profiles, Journal(state / "requests.sqlite"), credential_dir)
    Server(broker, account_uid("exo")).serve(socket.socket(fileno=3))


if __name__ == "__main__":
    main()

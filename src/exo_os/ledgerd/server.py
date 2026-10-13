"""Static-UID local signer; authenticate the caller before parsing any request."""
from __future__ import annotations

import os
import socket
import sqlite3
import threading

from exo import crypto
from exo.ledger_authority import AuthorityError, HeadConflict
from exo.ledger_authority.journal import AuthorityJournal

from ..egress.server import account_uid
from ..sysd.protocol import peer_credentials
from .policy import STATE, TIMEOUT, WORKERS, key_name, load_manifest, private_file
from .wire import GREETING, MAX_WIRE, decode, encode, read_line, request


class Server:
    def __init__(self, manifest, journals, accounts):
        self.manifest, self.journals = manifest, journals
        self.allowed = {(stream, writer): {accounts[name] for name in names}
                        for stream, entry in manifest["streams"].items()
                        for writer, names in entry["callers"].items()}
        self.caller_uids = set().union(*self.allowed.values())

    def serve_connection(self, conn):
        with conn:
            identifiers, entered_append = {}, False
            try:
                uid = peer_credentials(conn)[1]
                if uid not in self.caller_uids:
                    return
                conn.settimeout(TIMEOUT)
                conn.sendall(GREETING)
                raw = request(decode(read_line(conn, MAX_WIRE, TIMEOUT)))
                identifiers = {key: raw[key] for key in ("nonce", "stream", "writer")}
                if uid not in self.allowed.get((raw["stream"], raw["writer"]), set()):
                    raise AuthorityError("writer not authorized for this kernel identity")
                journal = self.journals[raw["stream"]]
                if raw["op"] == "append":
                    entered_append = True
                    result = journal.append(raw["writer"], raw["operation_id"], raw["expected_head"], raw["intent"])
                elif raw["op"] == "lookup":
                    result = journal.lookup(raw["operation_id"], writer=raw["writer"])
                elif raw["op"] == "head":
                    result = journal.head(raw["seq"])
                else:
                    result = journal.read_since(raw["seq"], raw["limit"])
                reply = {"version": 1, **identifiers, "state": "ok", "result": result}
            except HeadConflict as exc:
                reply = {"version": 1, **identifiers, "state": "conflict", "result": exc.head}
            except AuthorityError:
                reply = {"version": 1, **identifiers, "state": "unknown" if entered_append else "refused", "result": None}
            except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError):
                # A failed/absent reply after entering append never licenses a fresh operation ID.
                reply = {"version": 1, **identifiers, "state": "unknown" if entered_append else "refused", "result": None}
            try:
                encoded = encode(reply)
                if len(encoded) > MAX_WIRE:
                    return
                conn.sendall(encoded)
            except (OSError, ValueError):
                pass

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


def load_journals(manifest, uid, state=STATE):
    private_file(state / "private", uid, directory=True)
    journals = {}
    for stream, entry in manifest["streams"].items():
        directory = private_file(state / "private" / stream, uid, directory=True)
        for leaf in directory.iterdir():
            private_file(leaf, uid)
        keys = {writer: crypto.load_key(private_file(directory / key_name(writer), uid))
                for writer in entry["identity"]["writers"]}
        checkpoint = crypto.load_key(private_file(directory / "checkpoint.key", uid))
        journals[stream] = AuthorityJournal(private_file(directory / "journal.sqlite", uid),
                                           entry["identity"], keys, checkpoint)
    return journals


def main():
    os.umask(0o077)
    uid = account_uid("exo-ledger")
    if uid == 0 or os.geteuid() != uid:
        raise AuthorityError("ledger authority requires its static service UID")
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise AuthorityError("ledger authority requires its root-created systemd socket")
    manifest = load_manifest()
    accounts = {name: account_uid(name) for entry in manifest["streams"].values()
                for names in entry["callers"].values() for name in names}
    Server(manifest, load_journals(manifest, uid), accounts).serve(socket.socket(fileno=3))


if __name__ == "__main__":
    main()

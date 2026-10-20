"""Static-UID local signer; authenticate the caller before parsing any request."""
from __future__ import annotations

import hmac
import os
import socket
import sqlite3
import stat
import sys
import threading
from pathlib import Path

from exo import crypto
from exo import principal as core
from exo.ledger_authority import AuthorityError, HeadConflict
from exo.ledger_authority.journal import AuthorityJournal

from ..egress.server import account_uid
from ..sysd.protocol import peer_credentials
from .policy import CORE_BOUND, STATE, TIMEOUT, WORKERS, key_name, launch_record, load_manifest, private_file
from .wire import GREETING, MAX_WIRE, closing, decode, encode, read_request, request


def fd_path(fd):
    """The path the kernel names for an open descriptor, without asking its filesystem. On Linux a replaced or
    removed file reads as '... (deleted)'."""
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/self/fd/{fd}")
        if sys.platform == "darwin":  # host tests only: the signer ships for Linux
            import fcntl
            return fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0].decode()
    except (OSError, UnicodeDecodeError):
        pass
    return None


def store_device(home):
    """The filesystem of the core's home. The signer may stat it, though not enter it."""
    return os.stat(home).st_dev


def core_record(fd, expected: Path, uid: int) -> dict:
    """The core's launch record (D-060), read through a descriptor its caller sent (OS-21).

    The signer cannot open the record: it is private to the desktop user, in an owner-only folder. The caller
    opened it and passed the open file. It must be the private single-link file, owned by the caller, that the
    kernel names at the record's place on the store filesystem, so a copy, a replaced record or a file in another
    mount namespace does not count. The name is checked first, since it needs no call into the file's filesystem."""
    if fd_path(fd) != str(expected):
        raise AuthorityError("descriptor is not the core's current launch record")
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or info.st_mode & 0o077
            or info.st_size > core.RECORD_MAX or info.st_dev != store_device(expected.parents[1])):
        raise AuthorityError("descriptor is not the core's private launch record")
    raw = os.pread(fd, core.RECORD_MAX + 1, 0)
    if len(raw) > core.RECORD_MAX:
        raise AuthorityError("launch record too large")
    record = decode(raw)
    if not (isinstance(record, dict) and record.get("v") == 1 and type(record.get("pid")) is int
            and record["pid"] > 0 and type(record.get("uid")) is int and isinstance(record.get("start"), str)
            and record["start"]):
        raise AuthorityError("launch record is not a v1 record")
    return record


class Server:
    def __init__(self, manifest, journals, accounts):
        self.manifest, self.journals = manifest, journals
        self.allowed = {(stream, writer): {accounts[name] for name in names}
                        for stream, entry in manifest["streams"].items()
                        for writer, names in entry["callers"].items()}
        self.caller_uids = set().union(*self.allowed.values())
        self.records = {stream: launch_record(entry["ledger_path"]) for stream, entry in manifest["streams"].items()}

    def bind_core(self, stream, uid, peer, fds):
        """OS-21: only the process the core's launch record names may append as a core-bound writer.

        ``peer`` is the kernel's peer and its start identity, taken at accept. The record must name that pid,
        this uid and that start identity, and the pid must still be that process now."""
        if len(fds) != 1 or self.records[stream] is None:
            raise AuthorityError("this writer appends only with the core's launch record")
        record = core_record(fds[0], self.records[stream], uid)
        now = core.proc_info(peer.pid) if peer.pid else None
        if (peer.uid != uid or record["uid"] != uid or record["pid"] != peer.pid or peer.ident is None
                or now is None or not hmac.compare_digest(record["start"].encode(), peer.ident.encode())
                or not hmac.compare_digest(now.ident.encode(), peer.ident.encode())):
            raise AuthorityError("only the core process named by its launch record may append as this writer")

    def serve_connection(self, conn):
        fds = []  # descriptors the caller sent; closed however the connection ends
        with conn, closing(fds):
            identifiers, entered_append = {}, False
            try:
                uid = peer_credentials(conn)[1]
                if uid not in self.caller_uids:
                    return
                peer = core.accepted(conn)  # pid and start identity at accept, before any byte is read (D-060)
                conn.settimeout(TIMEOUT)
                conn.sendall(GREETING)
                raw = request(decode(read_request(conn, MAX_WIRE, TIMEOUT, fds)))
                identifiers = {key: raw[key] for key in ("nonce", "stream", "writer")}
                if uid not in self.allowed.get((raw["stream"], raw["writer"]), set()):
                    raise AuthorityError("writer not authorized for this kernel identity")
                if raw["writer"] in CORE_BOUND and raw["op"] == "append":
                    self.bind_core(raw["stream"], uid, peer, fds)
                elif fds:
                    raise AuthorityError("unexpected descriptor")
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
            try:
                threading.Thread(target=worker, args=(conn,), daemon=True).start()
            except BaseException:
                conn.close()
                slots.release()
                raise


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

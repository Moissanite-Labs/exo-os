"""Pinned identity client; authenticate listener and sending service before payload."""
from __future__ import annotations

import array
import grp
import os
import secrets
import socket
import stat
import struct
import time
from pathlib import Path

from exo.ledger_authority import (
    AppendOutcomeUnknown, AuthorityError, AuthorityUnavailable, HeadConflict, MAX_BATCH,
    verify_checkpoint, verify_record,
)

from ..egress.server import account_uid
from ..egress.policy import EgressError
from ..sandbox.policy import trusted_path
from ..sysd.protocol import peer_credentials
from .policy import SOCKET, TIMEOUT, load_manifest
from .wire import GREETING, MAX_WIRE, decode, encode, read_line, request


def verify_socket(path, listener_uid):
    trusted_path(Path(path).parent, directory=True)
    trusted_path(Path("/etc/group"))
    try:
        gid = grp.getgrnam("exo-sys").gr_gid
    except KeyError:
        raise AuthorityUnavailable("untrusted authority socket") from None
    info = Path(path).lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != listener_uid or stat.S_IMODE(info.st_mode) != 0o660
            or info.st_gid != gid):
        raise AuthorityUnavailable("untrusted authority socket")


def verify_greeting(conn, service_uid):
    greeting = bytearray()
    deadline = time.monotonic() + TIMEOUT
    while len(greeting) < len(GREETING):
        conn.settimeout(max(0.001, deadline - time.monotonic()))
        data, ancillary, flags, _ = conn.recvmsg(len(GREETING) - len(greeting), socket.CMSG_SPACE(12) + socket.CMSG_SPACE(64))
        peers, unexpected = [], False
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds = array.array("i")
                fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                for fd in fds:
                    os.close(fd)
                unexpected = True
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(payload) == 12:
                peers.append(struct.unpack("3i", payload))
            else:
                unexpected = True
        if (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or unexpected or len(peers) != 1
                or peers[0][1] != service_uid or not data or time.monotonic() > deadline):
            raise AuthorityUnavailable("authority service kernel identity mismatch")
        greeting.extend(data)
    if greeting != GREETING:
        raise AuthorityUnavailable("invalid authority greeting")


class AuthorityClient:
    def __init__(self, stream, writer, identity):
        from exo.ledger_authority import validate_identity
        self.stream, self.writer, self.identity = stream, writer, validate_identity(identity)
        if self.identity["stream_id"] != stream or writer not in self.identity["writers"]:
            raise AuthorityError("authority writer or stream is not pinned")

    def _call(self, op, **args):
        raw = request({"version": 1, "nonce": secrets.token_hex(16), "stream": self.stream,
                       "writer": self.writer, "op": op, **args})
        sent = False
        known_refusal = False
        try:
            listener_uid, service_uid = account_uid("root"), account_uid("exo-ledger")
            verify_socket(SOCKET, listener_uid)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(TIMEOUT)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                conn.connect(SOCKET)
                if peer_credentials(conn)[1] != listener_uid:
                    raise AuthorityUnavailable("authority listener kernel identity mismatch")
                verify_greeting(conn, service_uid)
                sent = True
                conn.sendall(encode(raw))
                reply = decode(read_line(conn, MAX_WIRE, TIMEOUT))
                if (not isinstance(reply, dict) or set(reply) != {"version", "nonce", "stream", "writer", "state", "result"}
                        or type(reply["version"]) is not int or reply["version"] != 1
                        or any(reply[k] != raw[k] for k in ("nonce", "stream", "writer"))
                        or reply["state"] not in {"ok", "refused", "unknown", "conflict"}):
                    raise AuthorityUnavailable("invalid authority response")
                if reply["state"] == "conflict":
                    verify_checkpoint(self.identity, reply["result"])
                    raise HeadConflict(reply["result"])
                if reply["state"] == "refused":
                    known_refusal = True
                    raise AuthorityError("authority refused this operation")
                if reply["state"] == "unknown":
                    raise AuthorityUnavailable("authority operation outcome unavailable")
                return reply["result"]
        except (HeadConflict, AuthorityError) as exc:
            if isinstance(exc, HeadConflict) or known_refusal:
                raise
            if sent and op == "append":
                raise AppendOutcomeUnknown(args["operation_id"]) from None
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            if sent and op == "append":
                raise AppendOutcomeUnknown(args["operation_id"]) from None
            raise AuthorityUnavailable("authority unavailable or unauthenticated") from exc

    def _record(self, record):
        if not isinstance(record, dict) or not isinstance(record.get("event"), dict):
            raise AuthorityError("invalid authority record")
        previous = self.head(record["event"]["seq"] - 1)
        verify_record(self.identity, record, previous)
        if record["checkpoint"]["writer"] != self.writer:
            raise AuthorityError("authority returned another writer's operation")
        return record

    def append(self, operation_id, expected_head, intent):
        record = self._call("append", operation_id=operation_id, expected_head=expected_head, intent=intent)
        try:
            record = self._record(record)
            if any(record["event"][key] != value for key, value in intent.items()):
                raise AuthorityError("authority returned another intent")
            return record
        except (AuthorityError, OSError, ValueError, KeyError, TypeError):
            raise AppendOutcomeUnknown(operation_id) from None

    def lookup(self, operation_id):
        record = self._call("lookup", operation_id=operation_id)
        return None if record is None else self._record(record)

    def head(self, seq=None):
        checkpoint = self._call("head", seq=seq)
        verify_checkpoint(self.identity, checkpoint)
        if seq is not None and checkpoint["seq"] != seq:
            raise AuthorityError("authority returned wrong checkpoint")
        return checkpoint

    def read_since(self, seq, limit=MAX_BATCH):
        records = self._call("read_since", seq=seq, limit=limit)
        if not isinstance(records, list) or len(records) > limit:
            raise AuthorityError("invalid replay batch")
        previous = self.head(seq)
        for record in records:
            verify_record(self.identity, record, previous)
            previous = record["checkpoint"]
        return records


class ReadAuthorityClient(AuthorityClient):
    def append(self, *args, **kwargs):
        raise AuthorityError("verification-only authority cannot append")


def create_authority(ledger_path, writer=None):
    # Identity comes only from the independent protected root manifest, never a reply.
    path = str(ledger_path)
    for stream, entry in load_manifest()["streams"].items():
        if path == entry["ledger_path"]:
            if writer is None:
                try:
                    candidates = [alias for alias, accounts in entry["callers"].items()
                                  if any(account_uid(name) == os.geteuid() for name in accounts)]
                except EgressError:
                    raise AuthorityUnavailable("no authorized verification identity") from None
                if not candidates:
                    raise AuthorityUnavailable("no authorized verification identity")
                return ReadAuthorityClient(stream, sorted(candidates)[0], entry["identity"])
            if writer in entry["identity"]["writers"]:
                return AuthorityClient(stream, writer, entry["identity"])
    raise AuthorityUnavailable("ledger projection or writer is not provisioned")

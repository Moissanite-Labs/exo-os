"""Authenticate both systemd's listener and the unprivileged service before sending."""
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

from ..sandbox.policy import trusted_path
from ..sysd.protocol import decode, encode, peer_credentials, read_line
from .policy import MAX_WIRE, TIMEOUT, EgressError, validate_request
from .server import account_uid

SOCKET = "/run/exo-egress.sock"


def verify_socket(path: str, listener_uid: int):
    trusted_path(Path(path).parent, directory=True)
    trusted_path(Path("/etc/group"))
    try:
        gid = grp.getgrnam("exo-sys").gr_gid
    except KeyError:
        raise EgressError("untrusted egress socket") from None
    info = Path(path).lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != listener_uid or
            stat.S_IMODE(info.st_mode) != 0o660 or info.st_gid != gid):
        raise EgressError("untrusted egress socket")


def verify_greeting(conn, service_uid):
    expected = b"exo-egress-v1\n"
    greeting = bytearray()
    deadline = time.monotonic() + TIMEOUT
    while len(greeting) < len(expected):
        conn.settimeout(max(0.001, deadline - time.monotonic()))
        data, ancillary, flags, _ = conn.recvmsg(
            len(expected) - len(greeting), socket.CMSG_SPACE(struct.calcsize("3i")) + socket.CMSG_SPACE(64))
        peers, unexpected = [], False
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors = array.array("i")
                descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
                for fd in descriptors:
                    os.close(fd)
                unexpected = True
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(payload) == struct.calcsize("3i"):
                peers.append(struct.unpack("3i", payload))
            else:
                unexpected = True
        if (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or unexpected or len(peers) != 1 or
                peers[0][1] != service_uid or not data or time.monotonic() > deadline):
            raise EgressError("egress service kernel identity mismatch")
        greeting.extend(data)
    if greeting != expected:
        raise EgressError("invalid egress greeting")


class EgressClient:
    def call(self, args):
        request = {"version": 1, "nonce": secrets.token_hex(16), "body": "", **args}
        sent = False
        try:
            validate_request(request)
            # uid=0 is the expected creator of the systemd .socket, not the service process.
            listener_uid, service_uid = account_uid("root"), account_uid("exo-egress")
            verify_socket(SOCKET, listener_uid)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(TIMEOUT * 3)
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                conn.connect(SOCKET)
                if peer_credentials(conn)[1] != listener_uid:
                    raise EgressError("egress listener kernel identity mismatch")
                verify_greeting(conn, service_uid)
                sent = True
                conn.sendall(encode(request))
                reply = decode(read_line(conn, MAX_WIRE, TIMEOUT * 3))
                if (not isinstance(reply, dict) or reply.get("version") != 1 or reply.get("nonce") != request["nonce"] or
                        reply.get("request_id") != request["request_id"] or reply.get("retryable") is not False or
                        reply.get("state") not in {"completed", "refused", "unknown"}):
                    raise EgressError("invalid egress response")
                return reply
        except (OSError, ValueError, RuntimeError):
            return {"request_id": request.get("request_id"), "state": "unknown" if sent else "refused", "retryable": False,
                    "message": "Reply lost or untrusted; operation may have completed. Never resend." if sent else
                               "Egress service unavailable or unauthenticated"}

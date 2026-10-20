"""One strict bounded request/reply per authenticated local connection."""
import array
import contextlib
import os
import socket
import time

from exo.ledger_authority import (
    AuthorityError, MAX_BATCH, MAX_DOCUMENT, NAME, canonical, validate_expected_head, validate_intent,
)
from ..sysd.protocol import decode, read_line

MAX_WIRE = MAX_DOCUMENT + 1
GREETING = b"exo-ledger-v1\n"
# Room for a few descriptors, so a sender that attaches more is seen and refused rather than truncated unseen.
ANCILLARY = socket.CMSG_SPACE(4 * array.array("i").itemsize)


def send_request(conn, data, fd=None):
    """Send one request line. A core-bound append carries the launch record's descriptor on its first bytes (OS-20);
    the JSON is the same either way."""
    if fd is None:
        conn.sendall(data)
        return
    sent = conn.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
    conn.sendall(data[sent:])


@contextlib.contextmanager
def closing(fds):
    """Close every descriptor in ``fds`` on the way out, including ones added inside the block."""
    try:
        yield fds
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def read_request(conn, limit, timeout, fds):
    """One JSON request line, as ``read_line``, and any descriptors sent with it, appended to ``fds``.

    The caller closes everything in ``fds``, also after an error. More than one descriptor, a truncated control
    message or any other ancillary data refuses."""
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("JSON line deadline exceeded")
        conn.settimeout(remaining)
        chunk, ancillary, flags, _ = conn.recvmsg(min(4096, limit + 1 - len(data)), ANCILLARY)
        unexpected = False
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                received = array.array("i")
                received.frombytes(payload[:len(payload) - len(payload) % received.itemsize])
                fds.extend(received)
            else:
                unexpected = True
        if unexpected or flags & socket.MSG_CTRUNC or len(fds) > 1:
            raise ValueError("unexpected ancillary data")
        if not chunk:
            raise ValueError("unterminated JSON line")
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("JSON line too large")
        if b"\n" in chunk:
            if not data.endswith(b"\n") or data.count(b"\n") != 1:
                raise ValueError("expected one JSON line")
            return bytes(data)


def encode(value):
    # Match the core's UTF-8 byte bounds, avoiding ASCII-escape expansion on replay.
    return canonical(value) + b"\n"


def request(raw):
    if not isinstance(raw, dict):
        raise AuthorityError("invalid authority request")
    common = {"version", "nonce", "stream", "writer", "op"}
    fields = {"append": {"operation_id", "expected_head", "intent"}, "lookup": {"operation_id"},
              "head": {"seq"}, "read_since": {"seq", "limit"}}
    if (not isinstance(raw.get("op"), str) or raw["op"] not in fields
            or set(raw) != common | fields[raw["op"]]
            or type(raw["version"]) is not int or raw["version"] != 1):
        raise AuthorityError("unsupported authority request")
    for key in ("nonce", "stream", "writer"):
        if not isinstance(raw[key], str) or not NAME.fullmatch(raw[key]):
            raise AuthorityError("invalid request identifier")
    if "operation_id" in raw and (not isinstance(raw["operation_id"], str) or not NAME.fullmatch(raw["operation_id"])):
        raise AuthorityError("invalid operation id")
    if raw["op"] == "append":
        validate_expected_head(raw["expected_head"])
        validate_intent(raw["intent"])
    if "seq" in raw and raw["seq"] is not None and (type(raw["seq"]) is not int or raw["seq"] < 0):
        raise AuthorityError("invalid sequence")
    if raw["op"] == "read_since" and (raw["seq"] is None or type(raw["limit"]) is not int or not 1 <= raw["limit"] <= MAX_BATCH):
        raise AuthorityError("invalid replay bound")
    # Defense in depth: encode() already bounds the document through canonical(),
    # so this cannot fire unless encode() changes. It stays as a second bound.
    if len(encode(raw)) > MAX_WIRE:
        raise AuthorityError("authority request exceeds bound")
    return raw


__all__ = ["closing", "decode", "encode", "read_line", "read_request", "request", "send_request", "MAX_WIRE",
           "GREETING"]

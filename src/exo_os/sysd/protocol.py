"""Small, bounded wire protocol shared by the root helper and its caller."""
from __future__ import annotations

import json
import socket
import struct
import time
from pathlib import Path

# Persistent and root-owned (created at boot by tmpfiles.d), so reconcile works before the helper starts.
PUBLIC_KEY = Path('/var/lib/exo-sysd-public/receipt.pub')
MAX_REQUEST = 64 * 1024
MAX_REPLY = 256 * 1024
VERSION = 1
NAMED_ACTIONS = frozenset({'status', 'snapshot.list', 'snapshot.create', 'package.install', 'snapshot.undo'})
MUTATIONS = NAMED_ACTIONS - {'status', 'snapshot.list'}


def peer_credentials(conn: socket.socket) -> tuple[int, int, int]:
    """Linux kernel credentials; never infer identity from a socket path or request."""
    if not hasattr(socket, 'SO_PEERCRED'):
        raise OSError('exo-sysd requires Linux SO_PEERCRED')
    return struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))


def read_line(conn: socket.socket, limit: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('JSON line deadline exceeded')
        conn.settimeout(remaining)
        chunk = conn.recv(min(4096, limit + 1 - len(data)))
        if not chunk:
            raise ValueError('unterminated JSON line')
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError('JSON line too large')
        if b'\n' in chunk:
            if not data.endswith(b'\n') or data.count(b'\n') != 1:
                raise ValueError('expected one JSON line')
            return bytes(data)


def decode(data: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate JSON key')
            out[key] = value
        return out

    def constant(value):
        raise ValueError('non-finite JSON number')

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'

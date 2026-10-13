"""Root-pinned stream, projection and writer inventory; no RPC-selected paths."""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path

from exo import crypto
from exo.ledger_authority import AuthorityError, NAME, validate_identity

from ..sandbox.policy import trusted_path

STATE = Path("/var/lib/exo-ledger")
MANIFEST = STATE / "manifest.json"
SOCKET = "/run/exo-ledger.sock"
STREAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
TIMEOUT = 10
WORKERS = 4


def validate_manifest(raw):
    if (not isinstance(raw, dict) or set(raw) != {"version", "streams"} or type(raw["version"]) is not int
            or raw["version"] != 1 or not isinstance(raw["streams"], dict) or not 1 <= len(raw["streams"]) <= 16):
        raise AuthorityError("invalid root authority manifest")
    paths = set()
    for stream, entry in raw["streams"].items():
        if (not isinstance(stream, str) or not STREAM.fullmatch(stream) or not isinstance(entry, dict)
                or set(entry) != {"ledger_path", "identity", "callers"}):
            raise AuthorityError("invalid stream policy")
        path = entry["ledger_path"]
        if (not isinstance(path, str) or not Path(path).is_absolute() or str(Path(path)) != path
                or ".." in Path(path).parts or path in paths):
            raise AuthorityError("invalid projection binding")
        paths.add(path)
        identity = validate_identity(entry["identity"])
        if len({crypto.unb64(writer["pub"]) for writer in identity["writers"].values()}) != len(identity["writers"]):
            raise AuthorityError("writer keys must be independently provisioned")
        if identity["stream_id"] != stream or not isinstance(entry["callers"], dict):
            raise AuthorityError("stream identity mismatch")
        if set(entry["callers"]) != set(identity["writers"]):
            raise AuthorityError("writer caller inventory mismatch")
        for writer, accounts in entry["callers"].items():
            if (not isinstance(accounts, list) or not accounts or
                    any(not isinstance(a, str) or not NAME.fullmatch(a) for a in accounts)):
                raise AuthorityError("invalid static caller account")
    return json.loads(json.dumps(raw, allow_nan=False))


def load_manifest(path=MANIFEST):
    trusted_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise AuthorityError("authority identity manifest unavailable")
    from ..sysd.protocol import decode
    return validate_manifest(decode(path.read_bytes()))


def private_file(path: Path, uid: int, *, directory=False):
    """Fixed private inventory must exist, be service-owned and have no symlink components."""
    if path != path.resolve(strict=True):
        raise AuthorityError("authority state contains a symlink")
    info = path.lstat()
    if (info.st_uid != uid or info.st_mode & 0o077 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise AuthorityError("authority state ownership or mode mismatch")
    return path


def key_name(writer):
    import hashlib
    return hashlib.sha256(writer.encode()).hexdigest() + ".key"

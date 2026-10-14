"""Root-owned descriptor-checked witness configuration and durable receipt evidence."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
from pathlib import Path

BASE = Path("/var/lib/exo/witness-publisher")
MARKER = "witness-enabled.json"
MAX_FILE = 65536
CREDENTIALS = ("ca.pem", "client.pem", "client.key")


class WitnessError(ValueError):
    def __init__(self, message, state="configuration_error"):
        super().__init__(message)
        self.state = state


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise WitnessError("duplicate configuration/evidence field")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WitnessError("invalid configuration/evidence JSON") from exc


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def check_node(info, *, directory=False, mode=None):
    if (info.st_uid != 0 or info.st_mode & 0o022 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
            (mode is not None and stat.S_IMODE(info.st_mode) != mode) or
            (not directory and info.st_nlink != 1)):
        raise WitnessError("witness path has unsafe type, ownership, links or mode")


@contextlib.contextmanager
def directory(path, *, mode=None):
    """Walk from / with openat/nofollow, retaining the checked final directory FD."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or str(path) != os.path.normpath(path):
        raise WitnessError("absolute normalized witness path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        check_node(os.fstat(fd), directory=True)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            check_node(os.fstat(fd), directory=True)
        check_node(os.fstat(fd), directory=True, mode=mode)
        yield fd
    finally:
        os.close(fd)


def read_file(fd, name, *, limit=MAX_FILE, mode=0o600):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    try:
        info = os.fstat(child)
        check_node(info, mode=mode)
        if info.st_size > limit:
            raise WitnessError("witness file exceeds limit")
        with os.fdopen(child, "rb", closefd=False) as source:
            raw = source.read(limit + 1)
        if len(raw) > limit or len(raw) != info.st_size:
            raise WitnessError("witness file exceeds limit or changed during read")
        return raw
    finally:
        os.close(child)


def exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def write_new(fd, name, raw):
    if len(raw) > MAX_FILE:
        raise WitnessError("witness evidence exceeds limit")
    child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=fd)
    try:
        with os.fdopen(child, "wb", closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(child)
    finally:
        os.close(child)


def replace_file(fd, name, raw):
    import secrets
    temporary = ".pending-" + secrets.token_hex(16)
    try:
        write_new(fd, temporary, raw)
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        if exists(fd, temporary):
            os.unlink(temporary, dir_fd=fd)


def validate_policy(value):
    import ipaddress
    import re

    from exo import crypto
    from exo.ledger_authority import HASH, validate_identity, verify_checkpoint
    fields = {"version", "identity", "genesis", "witness_pub", "host", "port", "server_hostname",
              "server_cert_sha256", "timeout"}
    if not isinstance(value, dict) or set(value) != fields or type(value["version"]) is not int or value["version"] != 1:
        raise WitnessError("invalid witness policy schema")
    identity = validate_identity(value["identity"])
    verify_checkpoint(identity, value["genesis"])
    pub = value["witness_pub"]
    if not isinstance(pub, str):
        raise WitnessError("pinned genesis and separate witness key required")
    witness_key = crypto.unb64(pub)
    if (value["genesis"]["seq"] != 0 or len(witness_key) != 32 or
            witness_key == crypto.unb64(identity["checkpoint_pub"]) or
            any(witness_key == crypto.unb64(w["pub"]) for w in identity["writers"].values())):
        raise WitnessError("pinned genesis and separate witness key required")
    if not isinstance(value["host"], str):
        raise WitnessError("numeric witness address required")
    ipaddress.ip_address(value["host"])
    if (type(value["port"]) is not int or not 1 <= value["port"] <= 65535 or
            not isinstance(value["server_hostname"], str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", value["server_hostname"]) or
            not isinstance(value["server_cert_sha256"], str) or not HASH.fullmatch(value["server_cert_sha256"]) or
            type(value["timeout"]) not in (int, float) or not 0.05 <= value["timeout"] <= 60):
        raise WitnessError("invalid witness endpoint, certificate pin or timeout")
    # These public transport documents must fit its fixed wire bound.
    if any(len(encoded(value[k])) > 8192 for k in ("identity", "genesis")):
        raise WitnessError("witness identity exceeds transport limit")
    return value


def policy_hash(policy):
    return hashlib.sha256(encoded(policy)).hexdigest()


def evidence(policy, checkpoint, receipt, target, observed_at):
    from exo.ledger_authority import digest
    return {"version": 1, "identity_hash": digest(policy["identity"]), "genesis_hash": digest(policy["genesis"]),
            "witness_pub": policy["witness_pub"], "checkpoint": checkpoint, "receipt": receipt,
            "authority_target": target, "observed_at": observed_at}


def validate_evidence(policy, value):
    from exo.ledger_authority import digest, verify_checkpoint
    from exo.ledger_authority.witness import verify_receipt
    fields = {"version", "identity_hash", "genesis_hash", "witness_pub", "checkpoint", "receipt", "authority_target", "observed_at"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["version"]) is not int or value["version"] != 1 or
            value["identity_hash"] != digest(policy["identity"]) or value["genesis_hash"] != digest(policy["genesis"]) or
            value["witness_pub"] != policy["witness_pub"] or not isinstance(value["observed_at"], str) or
            not 1 <= len(value["observed_at"]) <= 64):
        raise WitnessError("invalid witness receipt evidence")
    verify_receipt(policy["identity"], value["checkpoint"], value["receipt"], policy["witness_pub"])
    verify_checkpoint(policy["identity"], value["authority_target"])
    if value["authority_target"] != value["checkpoint"]:
        raise WitnessError("receipt evidence does not bind exactly its captured authority target")
    return value


def monotonic(previous, current):
    old, new = previous["receipt"], current["receipt"]
    if new["seq"] < old["seq"] or (new["seq"] == old["seq"] and new != old):
        raise WitnessError("witness receipt regressed or conflicts with saved evidence", "conflict")


@contextlib.contextmanager
def established(base=BASE):
    """Absent enrollment is disabled; partial or damaged enrollment never reseeds."""
    base = Path(base)
    with directory(base.parent) as parent:
        marked, present = exists(parent, MARKER), exists(parent, base.name)
        if not marked and not present:
            yield None
            return
        if not marked or not present:
            raise WitnessError("missing or partial enabled witness state requires administrator repair")
        marker = decode(read_file(parent, MARKER))
        with directory(base, mode=0o700) as fd:
            # Serialize publishing and initialization independently of mutable evidence inodes.
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            policy = validate_policy(decode(read_file(fd, "policy.json")))
            if (not isinstance(marker, dict) or set(marker) != {"version", "policy_hash"} or
                    type(marker["version"]) is not int or marker["version"] != 1 or marker["policy_hash"] != policy_hash(policy)):
                raise WitnessError("witness enable marker does not bind this policy")
            current = validate_evidence(policy, decode(read_file(fd, "current.json")))
            previous = validate_evidence(policy, decode(read_file(fd, "previous.json")))
            monotonic(previous, current)
            credentials = {name: read_file(fd, name) for name in CREDENTIALS}
            yield fd, policy, current, credentials


def persist(fd, policy, current, updated):
    validate_evidence(policy, updated)
    monotonic(current, updated)
    # Backup is durable before replacing current; no failure erases the previous verified receipt.
    replace_file(fd, "previous.json", encoded(current))
    replace_file(fd, "current.json", encoded(updated))

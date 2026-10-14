"""Explicit root-only fresh provisioning. No reseed, local-history import or remote sending."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

from exo import crypto
from exo.ledger_authority import AuthorityError, MAX_BATCH, canonical
from exo.ledger_authority.journal import AuthorityJournal

from ..egress.server import account_uid
from ..sandbox.policy import trusted_path
from .policy import STATE, key_name, load_manifest, validate_manifest


def inventory(principal):
    # The shipped single-user image's fixed attributions; other seats require explicit migration.
    return {name: {"author": name, "authors": [name]} for name in
            ("exo-gate", "exo-learn", "exo-os", "exo-onboard", "exo-consolidate", "principal", "agent", principal)} | {
                "ingest": {"author": "ingest", "authors": list(dict.fromkeys([
                    "ingest", principal, "principal", "chatgpt", "claude.ai", "claude-code", "grok"]))},
                "exo-learn": {"author": "exo-learn", "authors": list(dict.fromkeys(["exo-learn", principal, "principal"]))},
                "exo-memory": {"author": "exo-memory", "authors": list(dict.fromkeys(["exo-memory", principal, "principal"]))}}


def export_checkpoints(journal, since=0, limit=MAX_BATCH):
    """Bounded checkpoint-only reads, including genesis, up to one captured head."""
    if type(since) is not int or since < 0 or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise AuthorityError("invalid checkpoint export bounds")
    captured = journal.head()["seq"]
    for seq in range(since, min(captured + 1, since + limit)):
        yield journal.head(seq)


def _as_signer(uid, gid, action):
    """Open or create the journal only after dropping root privileges."""
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            action()
        except BaseException as exc:
            print(f"ledger signer validation failed: {exc}", file=sys.stderr, flush=True)
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise AuthorityError("signer validation or provisioning failed")


def _mount_identity(path):
    result = subprocess.run(["findmnt", "--json", "--mountpoint", str(path),
                             "--output", "TARGET,FSTYPE,FSROOT,UUID"],
                            check=True, capture_output=True, text=True)
    filesystems = json.loads(result.stdout).get("filesystems", [])
    if len(filesystems) != 1 or filesystems[0].get("target") != str(path):
        raise AuthorityError("required persistent mount is unavailable")
    return filesystems[0]


def validate_boot_mounts(state=STATE, store=Path("/var/lib/exo")):
    for path in (state, store):
        trusted_path(path, directory=True)
    signer, data = _mount_identity(state), _mount_identity(store)
    if (signer.get("fstype") != "btrfs" or data.get("fstype") != "btrfs"
            or signer.get("fsroot") != "/@signer" or data.get("fsroot") != "/@store"
            or not re.fullmatch(r"[0-9a-fA-F-]{36}", signer.get("uuid") or "")
            or signer["uuid"] != data.get("uuid")):
        raise AuthorityError("signer and store must be distinct persistent subvolumes on one btrfs filesystem")


def ensure_boot(principal="exo", *, state=STATE, store=Path("/var/lib/exo")):
    if os.geteuid() != 0 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", principal):
        raise AuthorityError("boot provisioning requires root and a supported principal name")
    validate_boot_mounts(state, store)
    projection = store / principal / "principals" / principal / "ledger.sqlite"
    uid = account_uid("exo-ledger")
    import pwd
    gid = pwd.getpwnam("exo-ledger").pw_gid
    manifest_path = state / "manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest = load_manifest(manifest_path)
        if len(manifest["streams"]) != 1 or next(iter(manifest["streams"].values()))["ledger_path"] != str(projection):
            raise AuthorityError("established signer is bound to another projection")
        from .server import load_journals
        _as_signer(uid, gid, lambda: load_journals(manifest, uid, state))
        return manifest
    # The image's @signer has no marker or half-created private state. Never infer
    # first boot from a missing manifest alone.
    if any(state.iterdir()):
        raise AuthorityError("partial signer state requires explicit administrator repair")
    skeleton = store / principal
    info = skeleton.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or any(skeleton.iterdir()):
        raise AuthorityError("fresh store must contain only its empty root-owned principal skeleton")
    if set(p.name for p in store.iterdir()) != {principal}:
        raise AuthorityError("fresh store has unexpected entries")
    return provision(principal, state=state, uid=uid, gid=gid, projection=projection)


def provision(principal, *, state=STATE, uid=None, gid=None, projection=None):
    if os.geteuid() != 0 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", principal):
        raise AuthorityError("fresh provisioning requires root and a supported principal name")
    trusted_path(state, directory=True)
    manifest_path, private = state / "manifest.json", state / "private"
    projection = projection or Path("/var/lib/exo/exo/principals") / principal / "ledger.sqlite"
    if manifest_path.exists() or manifest_path.is_symlink() or projection.exists() or projection.is_symlink():
        raise AuthorityError("established identity or local ledger requires explicit migration, never reseeding")
    if private.exists() and any(private.iterdir()):
        raise AuthorityError("partial or established signer state requires explicit repair")
    if private.is_symlink():
        raise AuthorityError("signer state cannot be a symlink")
    uid = account_uid("exo-ledger") if uid is None else uid
    if uid == 0:
        raise AuthorityError("signer cannot use root identity")
    if gid is None:
        import pwd
        gid = pwd.getpwnam("exo-ledger").pw_gid
    private.mkdir(mode=0o700, exist_ok=True)
    os.chown(private, uid, gid)
    os.chmod(private, 0o700)
    stream = "s_" + os.urandom(16).hex()
    directory = private / stream
    directory.mkdir(mode=0o700)
    keys = {name: crypto.keygen() for name in inventory(principal)}
    checkpoint = crypto.keygen()
    writers = {name: {**config, "pub": crypto.pubkey_b64(keys[name])} for name, config in inventory(principal).items()}
    identity = {"version": 1, "stream_id": stream, "checkpoint_pub": crypto.pubkey_b64(checkpoint), "writers": writers}
    os.chown(directory, uid, gid)
    def seed():
        for name, key in {**{key_name(writer): key for writer, key in keys.items()}, "checkpoint.key": checkpoint}.items():
            path = directory / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write((crypto.b64(bytes(key)) + "\n").encode())
                output.flush()
                os.fsync(output.fileno())
        AuthorityJournal.provision(directory / "journal.sqlite", identity, keys, checkpoint)
    _as_signer(uid, gid, seed)
    manifest = validate_manifest({"version": 1, "streams": {stream: {
        "ledger_path": str(projection), "identity": identity, "callers": {writer: ["exo"] for writer in writers}}}})
    # Published last, but partial private state also forbids retry if publication fails.
    fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as output:
        output.write(canonical(manifest) + b"\n")
        output.flush()
        os.fsync(output.fileno())
    os.chmod(manifest_path, 0o644)
    for path in (directory, private, state):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fresh = sub.add_parser("provision-fresh")
    fresh.add_argument("principal")
    boot = sub.add_parser("ensure-boot")
    boot.add_argument("--principal", default="exo")
    export = sub.add_parser("export-checkpoints")
    export.add_argument("--since", type=int, default=0)
    export.add_argument("--limit", type=int, default=MAX_BATCH)
    export.add_argument("--stream")
    export.add_argument("--raw", action="store_true", help="raw checkpoint JSONL for one independently pinned stream")
    args = parser.parse_args()
    if args.command == "provision-fresh":
        print(json.dumps(provision(args.principal)))
        return
    if args.command == "ensure-boot":
        ensure_boot(args.principal)
        return
    if os.geteuid() != 0:
        raise AuthorityError("checkpoint export requires an administrator")
    if args.raw and not args.stream:
        raise AuthorityError("raw export requires one explicit pinned stream")
    from .server import load_journals
    manifest = load_manifest()
    if args.stream and args.stream not in manifest["streams"]:
        raise AuthorityError("export stream is not provisioned")
    uid = account_uid("exo-ledger")
    import pwd
    os.setgroups([])
    os.setgid(pwd.getpwnam("exo-ledger").pw_gid)
    os.setuid(uid)
    for stream, journal in load_journals(manifest, uid).items():
        if args.stream and stream != args.stream:
            continue
        for checkpoint in export_checkpoints(journal, args.since, args.limit):
            print(json.dumps(checkpoint if args.raw else {"stream": stream, "checkpoint": checkpoint}, separators=(",", ":")))
    # stdout only. An independent witness must verify/pin and acknowledge separately.


if __name__ == "__main__":
    main()

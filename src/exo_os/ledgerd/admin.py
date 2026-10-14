"""Explicit root-only fresh provisioning. No reseed, local-history import or remote sending."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

from exo import crypto
from exo.ledger_authority import AuthorityError, MAX_BATCH, canonical
from exo.ledger_authority.journal import AuthorityJournal

from ..egress.server import account_uid
from ..sandbox.policy import SandboxError, trusted_path
from .policy import STATE, key_name, load_manifest, validate_manifest

REPAIR = "exo-ledger-admin repair"
# Boot units log these; the image installs no docs, so each names the command and its limit inline.
UNPUBLISHED_HINT = (f"root can run {REPAIR}, which sets this state aside and provisions again "
                    "only if nothing was signed or published")
PUBLISHED_HINT = "a published ledger is never reseeded and needs a reviewed migration"
REFUSED_HINT = "signed, published or unknown signer state is never reseeded and needs a reviewed migration"
VAULT = "repair"
TEMPORARY = re.compile(r"\.manifest-[0-9a-f]{16}")
STREAM_DIR = re.compile(r"s_[0-9a-f]{32}")
SIDE_FILES = {"journal.sqlite-wal", "journal.sqlite-shm", "journal.sqlite-journal"}
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


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


def _inode(path, kind, owners, forbidden=0o077):
    """One lstat: the exact file type, an expected owner and no forbidden mode bits."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise AuthorityError(f"signer state is missing: {path}") from exc
    if stat.S_IFMT(info.st_mode) != kind or info.st_uid not in owners or info.st_mode & forbidden:
        raise AuthorityError(f"signer state has wrong type, owner or mode: {path}")
    return info


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fresh_store(store, principal):
    skeleton = store / principal
    try:
        _inode(skeleton, stat.S_IFDIR, {0}, 0o022)
    except AuthorityError as exc:
        raise AuthorityError("fresh store must contain only its empty root-owned principal skeleton") from exc
    if any(skeleton.iterdir()):
        raise AuthorityError("fresh store must contain only its empty root-owned principal skeleton")
    if set(p.name for p in store.iterdir()) != {principal}:
        raise AuthorityError("fresh store has unexpected entries")


def _unsigned(journal):
    """Signer-UID evidence that a cut-off provision holds no event and no head above genesis."""
    try:
        db = sqlite3.connect(journal.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=10)
        try:
            db.execute("BEGIN")  # read-write lets SQLite recover its WAL first
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AuthorityError("partial signer journal integrity check failed")
            schema = db.execute("SELECT type, name FROM sqlite_master").fetchall()
            tables = {name for kind, name in schema if kind == "table"}
            if (not tables <= {"identity", "records", "operations", "checkpoints"}
                    or any(kind != "table" and not name.startswith("sqlite_autoindex_") for kind, name in schema)):
                raise AuthorityError("partial signer journal has an unexpected schema")
            for table, rows in (("records", ""), ("operations", ""), ("checkpoints", " WHERE seq != 0")):
                if table in tables and db.execute(f"SELECT 1 FROM {table}{rows} LIMIT 1").fetchone():
                    raise AuthorityError("partial signer journal holds signed history")
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise AuthorityError("partial signer journal is unreadable") from exc


def _discard_unpublished(principal, state, uid, gid):
    """Clear signer state left without a manifest, after proving nothing was signed or projected.

    Without a manifest the signer service cannot start, and the caller has checked
    that the store is still the empty skeleton with no projection. The private journal
    must also hold no event and no head above genesis. Only the names provision writes,
    and the owners and modes it sets at each step, in that order, are accepted.
    """
    names = {p.name for p in state.iterdir()}
    temporaries = {name for name in names if TEMPORARY.fullmatch(name)}
    if names - temporaries - {"private"} or len(temporaries) > 1:
        raise AuthorityError("partial signer state has unexpected entries")
    private = state / "private"
    leaves = {key_name(writer) for writer in inventory(principal)} | {"checkpoint.key", "journal.sqlite"} | SIDE_FILES
    streams, journals = [], 0
    if "private" in names:
        # Created by root, then given to the signer before any stream exists.
        _inode(private, stat.S_IFDIR, {0, uid})
        streams = [p.name for p in private.iterdir()]
        if len(streams) > 1 or any(not STREAM_DIR.fullmatch(name) for name in streams):
            raise AuthorityError("partial signer state has unexpected entries")
        if streams:
            _inode(private, stat.S_IFDIR, {uid})
    for stream in streams:
        directory = private / stream
        _inode(directory, stat.S_IFDIR, {0, uid})
        held = {p.name for p in directory.iterdir()}
        if not held <= leaves or (held & SIDE_FILES and "journal.sqlite" not in held):
            raise AuthorityError("partial signer stream has unexpected entries")
        if held:
            _inode(directory, stat.S_IFDIR, {uid})  # the signer writes only after it owns the directory
        for name in held:
            _inode(directory / name, stat.S_IFREG, {uid})
        if "journal.sqlite" in held:
            journals += 1
            try:
                _as_signer(uid, gid, lambda: _unsigned(directory / "journal.sqlite"))
            except AuthorityError as exc:
                raise AuthorityError("partial signer journal is not provably unsigned") from exc
    for name in temporaries:
        # Written after the journal. Nothing loads it while no manifest exists.
        info = _inode(state / name, stat.S_IFREG, {0}, 0o133)
        if not journals or info.st_nlink != 1:
            raise AuthorityError("partial signer state has an unverified manifest leftover")
    # Remove only what was checked, through held directory descriptors: a renamed or
    # replaced entry fails here instead of redirecting a root unlink. Removal runs in
    # the reverse of provisioning order, so a cut at any point leaves a state this
    # check accepts again: the temporary goes before the journal it requires, and
    # SQLite side files before their database, so none is ever left without it.
    state_fd = os.open(state, DIRECTORY)
    try:
        for name in temporaries:
            os.unlink(name, dir_fd=state_fd)
        os.fsync(state_fd)
        if "private" in names:
            private_fd = os.open("private", DIRECTORY, dir_fd=state_fd)
            try:
                for stream in os.listdir(private_fd):
                    if stream not in streams:
                        raise AuthorityError("partial signer state changed while it was cleared")
                    stream_fd = os.open(stream, DIRECTORY, dir_fd=private_fd)
                    try:
                        held = os.listdir(stream_fd)
                        if not set(held) <= leaves:
                            raise AuthorityError("partial signer state changed while it was cleared")
                        # Side files, then the database, then the keys.
                        for name in sorted(held, key=lambda leaf: (leaf not in SIDE_FILES, leaf != "journal.sqlite")):
                            os.unlink(name, dir_fd=stream_fd)
                            os.fsync(stream_fd)
                    finally:
                        os.close(stream_fd)
                    os.rmdir(stream, dir_fd=private_fd)
                os.fsync(private_fd)
            finally:
                os.close(private_fd)
            os.rmdir("private", dir_fd=state_fd)
        os.fsync(state_fd)
    finally:
        os.close(state_fd)


def _finish_publication(state, manifest_path):
    """After validation, remove the same-inode temporary link a crash right after publication leaves.

    Nothing reads any other leftover, and the validated manifest does not depend on
    it, so an unverified one stays in place for the administrator.
    """
    leftovers = [p for p in state.iterdir() if TEMPORARY.fullmatch(p.name)]
    if len(leftovers) != 1:
        return
    try:
        published = _inode(manifest_path, stat.S_IFREG, {0}, 0o133)
        linked = _inode(leftovers[0], stat.S_IFREG, {0}, 0o133)
    except AuthorityError:
        return
    if (all(stat.S_IMODE(info.st_mode) == 0o644 and info.st_nlink == 2 for info in (published, linked))
            and (published.st_dev, published.st_ino) == (linked.st_dev, linked.st_ino)):
        leftovers[0].unlink()
        _fsync_dir(state)


def _boot(principal, state, store, uid, gid):
    projection = store / principal / "principals" / principal / "ledger.sqlite"
    manifest_path = state / "manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        try:
            manifest = load_manifest(manifest_path)
        except (ValueError, SandboxError) as exc:  # includes a truncated or symlinked manifest
            raise AuthorityError(f"established signer manifest is invalid: {exc}") from exc
        if len(manifest["streams"]) != 1 or next(iter(manifest["streams"].values()))["ledger_path"] != str(projection):
            raise AuthorityError("established signer is bound to another projection")
        from .server import load_journals
        _as_signer(uid, gid, lambda: load_journals(manifest, uid, state))
        _finish_publication(state, manifest_path)
        return manifest
    # Never infer first boot from a missing manifest alone. The store must still be
    # the image's empty skeleton, so no projection exists, and any signer state must
    # prove that nothing was ever signed or published before it is cleared.
    _fresh_store(store, principal)
    if any(state.iterdir()):
        _discard_unpublished(principal, state, uid, gid)
    return provision(principal, state=state, uid=uid, gid=gid, projection=projection)


def _context(principal, state, store):
    if os.geteuid() != 0 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", principal):
        raise AuthorityError("boot provisioning requires root and a supported principal name")
    validate_boot_mounts(state, store)
    uid = account_uid("exo-ledger")
    import pwd
    gid = pwd.getpwnam("exo-ledger").pw_gid
    return uid, gid


def ensure_boot(principal="exo", *, state=STATE, store=Path("/var/lib/exo")):
    uid, gid = _context(principal, state, store)
    try:
        return _boot(principal, state, store, uid, gid)
    except AuthorityError as exc:
        hint = PUBLISHED_HINT if os.path.lexists(state / "manifest.json") else UNPUBLISHED_HINT
        raise AuthorityError(f"{exc}; {hint}") from exc


def _vault(path):
    if not os.path.lexists(path):
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
    _inode(path, stat.S_IFDIR, {0})
    return path


def _set_aside(parent, names, vault):
    """Move entries into a new root-only slot of a checked vault on the same subvolume; nothing is deleted."""
    slot = vault / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(4).hex())
    slot.mkdir(mode=0o700)  # never an existing directory, so a rename cannot replace evidence
    parent_fd = os.open(parent, DIRECTORY)
    slot_fd = os.open(slot, DIRECTORY)
    try:
        for name in sorted(names):
            os.rename(name, name, src_dir_fd=parent_fd, dst_dir_fd=slot_fd)
        os.fsync(slot_fd)
        os.fsync(parent_fd)
    finally:
        os.close(slot_fd)
        os.close(parent_fd)
    _fsync_dir(vault)
    _fsync_dir(vault.parent)
    return slot


def _kind(path, kind):
    """Repair's shape check: the file type alone, never following a symlink."""
    if stat.S_IFMT(path.lstat().st_mode) != kind:
        raise AuthorityError(f"repair cannot classify signer entry: {path}")


def _unpublished(principal, state, store, uid, gid):
    """Repair's proof that refused signer state holds nothing signed or published.

    Boot clears only the exact states provisioning leaves, because it deletes them.
    Repair deletes nothing, so it also takes entries with other owners or modes, more
    than one stream or temporary, and an earlier repair directory. It still needs the
    same evidence: no manifest, the empty store skeleton, so no projection exists,
    only entries provisioning writes, and journals with no event and no head above
    genesis under the signer UID. Established history is never reseeded.
    """
    if os.path.lexists(state / "manifest.json"):
        raise AuthorityError("a manifest was published")
    try:
        _fresh_store(store, principal)
    except AuthorityError as exc:
        raise AuthorityError("the store is not the empty first-boot skeleton") from exc
    leaves = {key_name(writer) for writer in inventory(principal)} | {"checkpoint.key", "journal.sqlite"} | SIDE_FILES
    journals, temporaries = [], 0
    for entry in state.iterdir():
        if entry.name == VAULT:
            continue  # checked as a root-only directory before anything moves into it
        if TEMPORARY.fullmatch(entry.name):
            _kind(entry, stat.S_IFREG)  # nothing reads a temporary manifest
            temporaries += 1
            continue
        if entry.name != "private":
            raise AuthorityError(f"repair cannot classify signer entry: {entry}")
        _kind(entry, stat.S_IFDIR)
        for stream in entry.iterdir():
            if not STREAM_DIR.fullmatch(stream.name):
                raise AuthorityError(f"repair cannot classify signer entry: {stream}")
            _kind(stream, stat.S_IFDIR)
            held = {p.name for p in stream.iterdir()}
            if not held <= leaves or (held & SIDE_FILES and "journal.sqlite" not in held):
                raise AuthorityError(f"repair cannot classify signer stream: {stream}")
            for name in held:
                _kind(stream / name, stat.S_IFREG)
            if "journal.sqlite" in held:
                journals.append(stream / "journal.sqlite")
    if temporaries and not journals:
        raise AuthorityError("a temporary manifest has no journal to show nothing was signed")
    for journal in journals:
        try:
            _as_signer(uid, gid, lambda journal=journal: _unsigned(journal))
        except AuthorityError as exc:
            raise AuthorityError("a signer journal is not provably unsigned") from exc


def repair(principal="exo", *, state=STATE, store=Path("/var/lib/exo")):
    """Root console repair after boot refused: preserve unpublished signer state, then provision again.

    Valid or resumable state is never replaced. State that may hold anything signed
    or published refuses unchanged, so no new genesis replaces established history.
    """
    uid, gid = _context(principal, state, store)
    try:
        return {"preserved": [], "refusal": None, "manifest": _boot(principal, state, store, uid, gid)}
    except AuthorityError as exc:
        refusal = str(exc)
    try:
        _unpublished(principal, state, store, uid, gid)
    except AuthorityError as exc:
        raise AuthorityError(f"{refusal}; repair refuses: {exc}; {REFUSED_HINT}") from exc
    vault = _vault(state / VAULT)
    signer = {p.name for p in state.iterdir()} - {VAULT}
    preserved = [_set_aside(state, signer, vault)] if signer else []
    projection = store / principal / "principals" / principal / "ledger.sqlite"
    manifest = provision(principal, state=state, uid=uid, gid=gid, projection=projection)
    return {"preserved": [str(path) for path in preserved], "refusal": refusal, "manifest": manifest}


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
    if any(p.name.startswith(".manifest-") for p in state.iterdir()):
        raise AuthorityError("partial signer state requires explicit repair")
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
    # Private state is durable before anything names it.
    for path in (directory, private, state):
        _fsync_dir(path)
    # Publish last and whole: a crash leaves either no manifest or a complete one,
    # never a truncated file. link(2) also never replaces an existing manifest.
    temporary = state / (".manifest-" + os.urandom(8).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(canonical(manifest) + b"\n")
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o644)
    fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temporary, manifest_path, follow_symlinks=False)
    except FileExistsError:
        raise AuthorityError("established identity at this path requires explicit migration") from None
    _fsync_dir(state)
    temporary.unlink()
    _fsync_dir(state)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fresh = sub.add_parser("provision-fresh")
    fresh.add_argument("principal")
    boot = sub.add_parser("ensure-boot")
    boot.add_argument("--principal", default="exo")
    fix = sub.add_parser("repair", help="root console only: preserve refused unpublished signer state, "
                                        "then provision a new stream")
    fix.add_argument("--principal", default="exo")
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
    if args.command == "repair":
        print(json.dumps(repair(args.principal), sort_keys=True))
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

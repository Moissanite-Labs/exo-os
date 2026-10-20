"""Root-controlled model-spend activation on the persistent Exo store.

Only a pristine first boot may create state. A first boot cut off before its policy
was published resumes only when first boot never finished and the journal shows no
use: no call, no stop and no observed clock. These checks cannot tell a policy that
was never written from one deleted before first boot finished. They show only that
no policy admitted a call through this journal. Later boots validate the same policy
and journal without resetting either ceilings or the journal identity. Every other
state refuses until root runs `repair`, which preserves it and starts a fresh journal.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import sqlite3
import stat
import sys
import time
import uuid
from pathlib import Path

from exo.ledger_authority import AuthorityError
from exo.model_spend import SpendJournal, decode_policy, validate_policy

from .ledgerd import admin as ledger_admin
from .ledgerd.policy import STATE as SIGNER_STATE
from .ledgerd.policy import load_manifest
from .sandbox.policy import trusted_path

STORE = Path("/var/lib/exo")
STATE = STORE / "model-spend"
POLICY = STATE / "policy.json"
JOURNAL = STATE / "exo" / "journal.sqlite"
PRINCIPAL = "exo"
REPAIR = "exo-model-spend-admin repair"
# Boot units log this; the image installs no docs, so it names the command inline.
REPAIR_HINT = f"root can run {REPAIR}, which preserves this state and publishes a fresh zero-cap journal"
VAULT = "model-spend-repair"
TEMPORARY = re.compile(r"\.policy-[0-9a-f]{16}")


def _node(path: Path, kind: int, mode: int, uid: int, gid: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AuthorityError(f"model-spend state is missing: {path}") from exc
    if (stat.S_IFMT(info.st_mode) != kind or stat.S_IMODE(info.st_mode) != mode
            or info.st_uid != uid or info.st_gid != gid):
        raise AuthorityError(f"model-spend state has wrong type, owner or mode: {path}")


def _node_one_of(path: Path, kind: int, *choices: tuple[int, int, int]) -> None:
    """The strict _node check, for the few (mode, uid, gid) states a cut-off first boot leaves."""
    for mode, uid, gid in choices:
        try:
            return _node(path, kind, mode, uid, gid)
        except AuthorityError as exc:
            refusal = exc
    raise refusal


def _entries(path: Path, expected: set[str]) -> None:
    if {p.name for p in path.iterdir()} != expected:
        raise AuthorityError(f"model-spend state has unexpected entries: {path}")


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _as_exo(uid: int, gid: int, action, *, result: bool = False):
    """Do SQLite work under the desktop UID; root never creates its database."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            os.close(read_fd)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            os.umask(0o077)
            value = action()
            if result:
                data = json.dumps(value, separators=(",", ":")).encode()
                if len(data) > 65536:
                    raise AuthorityError("model-spend helper result exceeds limit")
                while data:
                    written = os.write(write_fd, data)
                    if written <= 0:
                        raise OSError("model-spend helper pipe closed")
                    data = data[written:]
            code = 0
        except BaseException:  # child must never resume inherited root caller frames
            print("model-spend journal validation failed", file=sys.stderr, flush=True)
        finally:
            try:
                os.close(write_fd)
            except BaseException:
                code = 1
            finally:
                os._exit(code)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as pipe:
        data = pipe.read(65537)
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0 or len(data) > 65536:
        raise AuthorityError("model-spend journal validation or provisioning failed")
    if result:
        return json.loads(data)


def _policy(path: Path, journal: Path, principal: str) -> dict:
    _node(path, stat.S_IFREG, 0o644, 0, 0)
    if path.stat().st_size > 65536:
        raise AuthorityError("model-spend policy exceeds the size limit")
    try:
        raw = decode_policy(path.read_bytes())
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise AuthorityError("model-spend policy is invalid") from exc
    if raw["principal"] != principal or raw["journal"] != str(journal):
        raise AuthorityError("model-spend policy is bound to another principal or journal")
    return raw


def _publication_link(state: Path, principal: str) -> Path | None:
    """Accept only the one exact hard link left by completed policy publication."""
    names = {p.name for p in state.iterdir()}
    expected = {"policy.json", principal}
    if not expected <= names:
        raise AuthorityError(f"model-spend state has unexpected entries: {state}")
    policy = (state / "policy.json").lstat()
    if names == expected:
        if policy.st_nlink != 1:
            raise AuthorityError("model-spend policy has unexpected hard links")
        return None
    extras = names - expected
    if len(extras) != 1:
        raise AuthorityError(f"model-spend state has unexpected entries: {state}")
    name = next(iter(extras))
    if not TEMPORARY.fullmatch(name):
        raise AuthorityError("model-spend policy publication link has an invalid name")
    temporary = state / name
    _node(temporary, stat.S_IFREG, 0o644, 0, 0)
    linked = temporary.lstat()
    if (policy.st_nlink != 2 or linked.st_nlink != 2
            or (policy.st_dev, policy.st_ino) != (linked.st_dev, linked.st_ino)):
        raise AuthorityError("model-spend policy publication link does not match the published policy")
    return temporary


def _ledger_ready(principal: str, store: Path, signer: Path) -> None:
    ledger_admin.validate_boot_mounts(signer, store)
    manifest_path = signer / "manifest.json"
    if not manifest_path.exists() or manifest_path.is_symlink():
        raise AuthorityError("protected ledger identity must be provisioned first")
    manifest = load_manifest(manifest_path)
    projection = store / principal / "principals" / principal / "ledger.sqlite"
    if (len(manifest["streams"]) != 1
            or next(iter(manifest["streams"].values()))["ledger_path"] != str(projection)):
        raise AuthorityError("protected ledger identity does not match the principal store")
    # The manifest is present, so this enters only the existing-state branch:
    # validate the private signer journal without ever reseeding it.
    ledger_admin.ensure_boot(principal=principal, state=signer, store=store)


def _journal_dir(user_dir: Path, uid: int, gid: int, *, empty: bool = False) -> set[str]:
    """The journal parent holds the database and, after a power loss, its hot rollback journal."""
    names = {p.name for p in user_dir.iterdir()}
    if names not in ({"journal.sqlite"}, {"journal.sqlite", "journal.sqlite-journal"}) and (names or not empty):
        raise AuthorityError("model-spend journal directory has unexpected entries")
    if names:
        _node(user_dir / "journal.sqlite", stat.S_IFREG, 0o600, uid, gid)
    if "journal.sqlite-journal" in names:
        hot = user_dir / "journal.sqlite-journal"
        info = hot.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise AuthorityError("model-spend rollback journal has unsafe ownership or mode")
        _node(hot, stat.S_IFREG, stat.S_IMODE(info.st_mode), uid, gid)  # exact owner; any private mode
    return names


def _established(state: Path, principal: str, uid: int, gid: int) -> dict:
    policy_path, user_dir, journal = state / "policy.json", state / principal, state / principal / "journal.sqlite"
    _node(state, stat.S_IFDIR, 0o755, 0, 0)
    temporary = _publication_link(state, principal)
    _node(user_dir, stat.S_IFDIR, 0o700, uid, gid)
    _journal_dir(user_dir, uid, gid)
    raw = _policy(policy_path, journal, principal)
    # Normal constructor opens the existing journal read/write under exo UID,
    # allowing SQLite crash recovery. The separate status CLI uses inspect(),
    # whose connections remain read-only.
    _as_exo(uid, gid, lambda: SpendJournal(raw).status())
    if temporary is not None:
        # Only boot activation repairs a completed publication, after both the
        # root policy and its original journal identity have been validated.
        if _publication_link(state, principal) != temporary:
            raise AuthorityError("model-spend policy publication link changed during validation")
        temporary.unlink()
        _fsync(state)
    return raw


def _unpublished_journal(journal: Path, principal: str) -> str | None:
    """Exo-UID evidence that first boot left this journal as provisioned, or before any identity.

    Returns the identity only for an untouched journal: no call, no stop and no
    observed clock. Returns None when no identity row was ever committed.
    """
    try:
        db = sqlite3.connect(journal.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=10)
        try:
            db.execute("BEGIN")  # read-write lets SQLite roll back a hot journal first
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AuthorityError("model-spend journal integrity check failed")
            schema = db.execute("SELECT type, name FROM sqlite_master").fetchall()
            tables = {name for kind, name in schema if kind == "table"}
            if (not tables <= {"identity", "calls"}
                    or any(kind != "table" and not name.startswith("sqlite_autoindex_") for kind, name in schema)):
                raise AuthorityError("model-spend journal has an unexpected schema")
            if "calls" in tables and db.execute("SELECT 1 FROM calls LIMIT 1").fetchone():
                raise AuthorityError("model-spend journal without a policy holds calls")
            rows = (db.execute("SELECT id, principal, blocked, observed FROM identity").fetchall()
                    if "identity" in tables else [])
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise AuthorityError("model-spend journal is unreadable") from exc
    if not rows:
        return None
    if tables != {"identity", "calls"} or len(rows) != 1 or rows[0][1:] != (principal, "", 0):
        raise AuthorityError("model-spend journal without a policy was used or bound elsewhere")
    return _journal_id(rows[0][0])


def _journal_id(value) -> str:
    """Only the canonical UUID text SpendJournal itself compares against the policy."""
    try:
        if isinstance(value, str) and str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise AuthorityError("model-spend journal identity is invalid")


def _discard_unbound(store: Path, state: Path, principal: str) -> None:
    """Remove first-boot directories whose journal never committed an identity.

    The rollback journal goes before its database, so a cut never leaves it alone,
    which the next boot would refuse.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    state_fd = os.open(state, flags)
    try:
        if principal in os.listdir(state_fd):
            user_fd = os.open(principal, flags, dir_fd=state_fd)
            try:
                names = os.listdir(user_fd)
                if not set(names) <= {"journal.sqlite", "journal.sqlite-journal"}:
                    raise AuthorityError("model-spend journal directory has unexpected entries")
                for name in sorted(names, reverse=True):  # journal.sqlite-journal, then journal.sqlite
                    os.unlink(name, dir_fd=user_fd)
                    os.fsync(user_fd)
            finally:
                os.close(user_fd)
            os.rmdir(principal, dir_fd=state_fd)
        os.fsync(state_fd)
    finally:
        os.close(state_fd)
    state.rmdir()
    _fsync(store)


def _resume(store: Path, state: Path, principal: str, uid: int, gid: int) -> dict:
    """Finish a first boot cut off before policy.json was published.

    Login waits for this unit and the core opens the journal only through policy.json.
    Absent .owned shows first boot never finished. The journal rows must show it was
    never used: no call, no stop and no observed clock. Nothing reads a temporary
    policy. Accept only the states first boot leaves at each step, then publish the
    same zero caps it would have.
    """
    if os.path.lexists(store / ".owned"):
        raise AuthorityError("model-spend policy is missing after first boot completed")
    _fresh(store, principal, {principal, "model-spend"})
    # The unit's UMask=0077 leaves 0700 until the explicit chmod.
    _node_one_of(state, stat.S_IFDIR, (0o755, 0, 0), (0o700, 0, 0))
    names = {p.name for p in state.iterdir()}
    temporaries = names - {principal}
    if len(temporaries) > 1 or any(not TEMPORARY.fullmatch(name) for name in temporaries):
        raise AuthorityError(f"model-spend state has unexpected entries: {state}")
    if names:
        _node(state, stat.S_IFDIR, 0o755, 0, 0)
    user_dir, journal = state / principal, state / principal / "journal.sqlite"
    journal_id = None
    if principal in names:
        _node_one_of(user_dir, stat.S_IFDIR, (0o700, uid, gid), (0o700, 0, 0))
        if _journal_dir(user_dir, uid, gid, empty=True):
            _node(user_dir, stat.S_IFDIR, 0o700, uid, gid)
            journal_id = _as_exo(uid, gid, lambda: _unpublished_journal(journal, principal), result=True)
            if journal_id is not None:
                _journal_id(journal_id)
            _journal_dir(user_dir, uid, gid)
    for name in temporaries:
        # Written only after the journal identity exists; nothing loads it, so it is discarded.
        if journal_id is None:
            raise AuthorityError("model-spend temporary policy has no provisioned journal")
        _node_one_of(state / name, stat.S_IFREG, (0o644, 0, 0), (0o600, 0, 0))
        if (state / name).lstat().st_nlink != 1:
            raise AuthorityError("model-spend temporary policy has unexpected hard links")
        (state / name).unlink()
    if journal_id is None:
        _discard_unbound(store, state, principal)
        _fresh(store, principal, {principal})
        return _provision(store, state, principal, uid, gid)
    _publish(store, state, _zero_policy(principal, journal, journal_id))
    return _established(state, principal, uid, gid)


def _fresh(store: Path, principal: str, entries: set[str]) -> None:
    skeleton = store / principal
    _node(skeleton, stat.S_IFDIR, 0o700, 0, 0)
    _entries(skeleton, set())
    _entries(store, entries)


def _zero_policy(principal: str, journal: Path, identity: str) -> dict:
    raw = {"principal": principal, "journal": str(journal), "journal_id": identity,
           "daily_usd": "0", "monthly_usd": "0", "routes": {}}
    validate_policy(raw)
    return raw


def _provision(store: Path, state: Path, principal: str, uid: int, gid: int) -> dict:
    state.mkdir(mode=0o755)
    os.chmod(state, 0o755)
    user_dir = state / principal
    user_dir.mkdir(mode=0o700)
    os.chown(user_dir, uid, gid)
    journal = user_dir / "journal.sqlite"

    def seed() -> str:
        identity = SpendJournal.provision(journal, principal)
        _fsync(journal)
        _fsync(user_dir)
        return identity

    identity = _as_exo(uid, gid, seed, result=True)
    _node(journal, stat.S_IFREG, 0o600, uid, gid)
    raw = _zero_policy(principal, journal, identity)
    _fsync(user_dir)
    _publish(store, state, raw)
    return _established(state, principal, uid, gid)


def _publish(store: Path, state: Path, raw: dict) -> None:
    # Publish the root-controlled policy last. A crash before this point leaves
    # state that _resume accepts only while it proves nothing was published.
    # After publication, only its verified same-inode temporary hard link is recoverable.
    policy = state / "policy.json"
    temporary = state / (".policy-" + os.urandom(8).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o644)
    _fsync(temporary)
    # link(2) publishes atomically and fails if another root actor placed a
    # policy meanwhile. Never replace an established cap file.
    os.link(temporary, policy, follow_symlinks=False)
    _fsync(state)
    temporary.unlink()
    _fsync(state)
    _fsync(store)


def _boot(store: Path, state: Path, principal: str, uid: int, gid: int) -> dict:
    if os.path.lexists(state):
        if not os.path.lexists(state / "policy.json"):
            return _resume(store, state, principal, uid, gid)
        return _established(state, principal, uid, gid)
    # Absence after ownership transfer is loss, not a new first boot. The
    # ledger unit still sees the root-owned empty skeleton on a fresh boot.
    if os.path.lexists(store / ".owned"):
        raise AuthorityError("missing established model-spend state requires explicit repair")
    _fresh(store, principal, {principal})
    return _provision(store, state, principal, uid, gid)


def _context(principal: str, store: Path, signer: Path) -> tuple[Path, int, int]:
    if os.geteuid() != 0 or principal != PRINCIPAL:
        raise AuthorityError("model-spend boot activation requires root and the fixed exo principal")
    trusted_path(store, directory=True)
    _ledger_ready(principal, store, signer)
    account = pwd.getpwnam(principal)
    uid, gid = account.pw_uid, account.pw_gid
    if uid == 0:
        raise AuthorityError("model-spend journal cannot use the root identity")
    return store / "model-spend", uid, gid


def ensure_boot(principal: str = PRINCIPAL, *, store: Path = STORE, signer: Path = SIGNER_STATE) -> dict:
    state, uid, gid = _context(principal, store, signer)
    try:
        return _boot(store, state, principal, uid, gid)
    except AuthorityError as exc:
        raise AuthorityError(f"{exc}; {REPAIR_HINT}") from exc


def repair(principal: str = PRINCIPAL, *, store: Path = STORE, signer: Path = SIGNER_STATE) -> dict:
    """Root console repair after boot refused: preserve that state, then publish fresh zero caps.

    State that passes the boot check, including a stopped journal, is never replaced.
    The preserved policy and journal stay root-only for review. This is the one path
    where a fresh journal follows a published policy.
    """
    state, uid, gid = _context(principal, store, signer)
    try:
        return {"preserved": None, "refusal": None, "policy": _boot(store, state, principal, uid, gid)}
    except AuthorityError as exc:
        refusal = str(exc)
    preserved = None
    if os.path.lexists(state):
        vault = store / VAULT
        if not os.path.lexists(vault):
            vault.mkdir(mode=0o700)
            os.chmod(vault, 0o700)
        _node(vault, stat.S_IFDIR, 0o700, 0, 0)
        slot = vault / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + os.urandom(4).hex())
        slot.mkdir(mode=0o700)  # never an existing directory, so rename cannot replace evidence
        preserved = slot / "model-spend"
        os.rename(state, preserved)  # same @store subvolume; moves a symlink itself, never its target
        for path in (slot, vault, store):
            _fsync(path)
    return {"preserved": None if preserved is None else str(preserved), "refusal": refusal,
            "policy": _provision(store, state, principal, uid, gid)}


def inspect(*, store: Path = STORE, principal: str = PRINCIPAL) -> dict:
    """A read-only root inspection, including an explicitly stopped journal."""
    if os.geteuid() != 0 or principal != PRINCIPAL:
        raise AuthorityError("model-spend inspection requires root")
    trusted_path(store, directory=True)
    state = store / "model-spend"
    account = pwd.getpwnam(principal)
    uid, gid = account.pw_uid, account.pw_gid
    _node(state, stat.S_IFDIR, 0o755, 0, 0)
    _node(state / principal, stat.S_IFDIR, 0o700, uid, gid)
    _journal_dir(state / principal, uid, gid)
    raw = _policy(state / "policy.json", state / principal / "journal.sqlite", principal)
    _publication_link(state, principal)  # inspect never removes the verified leftover

    status = _as_exo(uid, gid, lambda: SpendJournal.inspect(raw), result=True)
    return {**status, "journal_id": raw["journal_id"], "journal": raw["journal"],
            "policy": str(state / "policy.json")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-boot")
    sub.add_parser("status")
    sub.add_parser("repair", help="root console only: preserve refused state, then publish fresh zero caps")
    args = parser.parse_args()
    if args.command == "ensure-boot":
        ensure_boot()
    elif args.command == "repair":
        print(json.dumps(repair(), sort_keys=True))
    else:
        print(json.dumps(inspect(), sort_keys=True))


if __name__ == "__main__":
    main()

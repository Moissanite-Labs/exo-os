"""Root-controlled model-spend activation on the persistent Exo store.

Only a pristine first boot may create state. Later boots validate the same policy
and journal without resetting either ceilings or the journal identity.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import stat
import sys
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


def _node(path: Path, kind: int, mode: int, uid: int, gid: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AuthorityError(f"model-spend state is missing: {path}") from exc
    if (stat.S_IFMT(info.st_mode) != kind or stat.S_IMODE(info.st_mode) != mode
            or info.st_uid != uid or info.st_gid != gid):
        raise AuthorityError(f"model-spend state has wrong type, owner or mode: {path}")


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
    if not re.fullmatch(r"\.policy-[0-9a-f]{16}", name):
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


def _established(state: Path, principal: str, uid: int, gid: int) -> dict:
    policy_path, user_dir, journal = state / "policy.json", state / principal, state / principal / "journal.sqlite"
    _node(state, stat.S_IFDIR, 0o755, 0, 0)
    temporary = _publication_link(state, principal)
    _node(user_dir, stat.S_IFDIR, 0o700, uid, gid)
    # A hot SQLite rollback journal may remain after an abrupt power loss.
    names = {p.name for p in user_dir.iterdir()}
    if names not in ({"journal.sqlite"}, {"journal.sqlite", "journal.sqlite-journal"}):
        raise AuthorityError("model-spend journal directory has unexpected entries")
    _node(journal, stat.S_IFREG, 0o600, uid, gid)
    if "journal.sqlite-journal" in names:
        info = (user_dir / "journal.sqlite-journal").lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o077:
            raise AuthorityError("model-spend rollback journal has unsafe ownership or mode")
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


def ensure_boot(principal: str = PRINCIPAL, *, store: Path = STORE, signer: Path = SIGNER_STATE) -> dict:
    if os.geteuid() != 0 or principal != PRINCIPAL:
        raise AuthorityError("model-spend boot activation requires root and the fixed exo principal")
    trusted_path(store, directory=True)
    _ledger_ready(principal, store, signer)
    state = store / "model-spend"
    account = pwd.getpwnam(principal)
    uid, gid = account.pw_uid, account.pw_gid
    if uid == 0:
        raise AuthorityError("model-spend journal cannot use the root identity")
    if state.exists() or state.is_symlink():
        return _established(state, principal, uid, gid)

    # Absence after ownership transfer is loss, not a new first boot. The
    # ledger unit still sees the root-owned empty skeleton on a fresh boot.
    if (store / ".owned").exists() or (store / ".owned").is_symlink():
        raise AuthorityError("missing established model-spend state requires explicit repair")
    skeleton = store / principal
    _node(skeleton, stat.S_IFDIR, 0o700, 0, 0)
    _entries(skeleton, set())
    _entries(store, {principal})

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
    raw = {"principal": principal, "journal": str(journal), "journal_id": identity,
           "daily_usd": "0", "monthly_usd": "0", "routes": {}}
    validate_policy(raw)
    _fsync(user_dir)
    # Publish the root-controlled policy last. A crash before this point leaves
    # partial state which ensure_boot deliberately refuses on the next boot.
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
    return _established(state, principal, uid, gid)


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
    names = {p.name for p in (state / principal).iterdir()}
    if names not in ({"journal.sqlite"}, {"journal.sqlite", "journal.sqlite-journal"}):
        raise AuthorityError("model-spend journal directory has unexpected entries")
    _node(state / principal / "journal.sqlite", stat.S_IFREG, 0o600, uid, gid)
    if "journal.sqlite-journal" in names:
        info = (state / principal / "journal.sqlite-journal").lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid or info.st_mode & 0o077:
            raise AuthorityError("model-spend rollback journal has unsafe ownership or mode")
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
    args = parser.parse_args()
    if args.command == "ensure-boot":
        ensure_boot()
    else:
        print(json.dumps(inspect(), sort_keys=True))


if __name__ == "__main__":
    main()

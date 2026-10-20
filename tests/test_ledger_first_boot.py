"""First-boot ledger provisioning cut at each step, refusals and root repair on a simulated signer mount."""
from __future__ import annotations

import json
import os
import pwd
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from exo.ledger_authority import AuthorityError
from fake_owners import FakeOwners

from exo_os.ledgerd import admin, policy, server

SIGNER, SIGNER_GID = 4201, 4202  # neither root nor the test user, so every owner check runs


class PowerCut(Exception):
    """Nothing after the injected point runs, as after a power loss."""


def _cut(*args, **kwargs):
    raise PowerCut()


@pytest.fixture
def boot(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    state, store = root / "signer", root / "store"
    for path in (state, store):  # as the image installs them, so the signer UID can enter the signer mount
        path.mkdir(mode=0o755)
        path.chmod(0o755)
    (store / "exo").mkdir(mode=0o700)
    # Root owns what root creates; the signer owns what appears under its UID drop.
    owners = FakeOwners(root, monkeypatch)
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    monkeypatch.setattr(admin, "validate_boot_mounts", lambda *a: None)
    monkeypatch.setattr(admin, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(policy, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(admin, "account_uid", lambda name: SIGNER)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_gid=SIGNER_GID))
    monkeypatch.setattr(admin, "_as_signer", lambda uid, gid, action: owners.run_as(uid, gid, action))
    return SimpleNamespace(state=state, store=store, owners=owners,
                           projection=store / "exo/principals/exo/ledger.sqlite")


def _boot_as_unit(boot):
    """The provisioning unit runs with UMask=0077."""
    previous = os.umask(0o077)
    try:
        return admin.ensure_boot(state=boot.state, store=boot.store)
    finally:
        os.umask(previous)


def _as_signer(boot, action):
    """Test-side journal work runs under the signer UID, as the service's would."""
    result = []
    boot.owners.run_as(SIGNER, SIGNER_GID, lambda: result.append(action()))
    return result[0]


def _journal_call(boot, manifest, call):
    stream = next(iter(manifest["streams"]))
    return _as_signer(boot, lambda: call(server.load_journals(manifest, SIGNER, boot.state)[stream]))


def _head(boot, manifest):
    return _journal_call(boot, manifest, lambda journal: journal.head())


def _append(boot, manifest):
    def append(journal):
        head = journal.head()
        return journal.append("exo-os", "op-1", {"seq": head["seq"], "chain": head["chain"]},
                              {"author": "exo-os", "kind": "action", "body": {"step": 1}, "session": "s", "refs": []})
    return _journal_call(boot, manifest, append)


def _sql(boot, path, statement):
    def run():
        db = sqlite3.connect(path)
        try:
            rows = db.execute(statement).fetchall()
            db.commit()
            return rows
        finally:
            db.close()
    return _as_signer(boot, run)


def _count(boot, path, table):
    return _sql(boot, path, f"SELECT COUNT(*) FROM {table}")[0][0]


def _tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in sorted(root.rglob("*"))}


def _empty_journal(path, identity, keys, checkpoint):
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    raise PowerCut()


def _schema_without_genesis(path, identity, keys, checkpoint):
    from exo.ledger_authority.journal import SCHEMA
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.close()
    raise PowerCut()


def _torn_key_writes(real, survive):
    """Let `survive` key files complete, then cut while writing the next one."""
    calls = []

    def opener(fd, *args, **kwargs):
        handle = real(fd, *args, **kwargs)
        calls.append(fd)
        if len(calls) <= survive:
            return handle

        class Torn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                handle.close()

            def write(self, data):
                handle.write(data[:len(data) // 2])
                handle.flush()
                raise PowerCut()
        return Torn()
    return opener


def _cut_paths(real, prefix):
    def call(path, *args, **kwargs):
        if Path(path).name.startswith(prefix):
            raise PowerCut()
        return real(path, *args, **kwargs)
    return call


def _cut_on_call(real, number):
    seen = []

    def call(*args, **kwargs):
        seen.append(args)
        if len(seen) == number:
            raise PowerCut()
        return real(*args, **kwargs)
    return call


def _torn_manifest(patch):
    canonical = admin.canonical
    patch.setattr(admin, "canonical", lambda value: canonical(value)[:57])
    patch.setattr(admin.os, "chmod", _cut_paths(os.chmod, ".manifest-"))


# Each power-cut point in provision(), before the manifest is linked into place.
UNPUBLISHED_CUTS = {
    "after_private_mkdir": lambda patch: patch.setattr(admin.os, "chown", _cut),
    "after_private_owned": lambda patch: patch.setattr(admin.os, "chmod", _cut),
    "after_stream_mkdir": lambda patch: patch.setattr(admin.os, "chown", _cut_on_call(os.chown, 2)),
    "torn_first_key": lambda patch: patch.setattr(admin.os, "fdopen", _torn_key_writes(os.fdopen, 0)),
    "torn_later_key": lambda patch: patch.setattr(admin.os, "fdopen", _torn_key_writes(os.fdopen, 4)),
    "after_keys_written": lambda patch: patch.setattr(admin.AuthorityJournal, "provision", _cut),
    "empty_journal_file": lambda patch: patch.setattr(admin.AuthorityJournal, "provision", _empty_journal),
    "journal_schema_without_genesis": lambda patch: patch.setattr(admin.AuthorityJournal, "provision",
                                                                  _schema_without_genesis),
    "after_journal_provisioned": lambda patch: patch.setattr(admin, "validate_manifest", _cut),
    "torn_manifest_write": _torn_manifest,
    "manifest_before_chmod": lambda patch: patch.setattr(admin.os, "chmod", _cut_paths(os.chmod, ".manifest-")),
    "manifest_before_link": lambda patch: patch.setattr(admin.os, "link", _cut),
}


def _assert_fresh_stream(boot, manifest, left=()):
    stream = next(iter(manifest["streams"]))
    assert stream not in left
    assert {p.name for p in boot.state.iterdir()} - {admin.VAULT} == {"manifest.json", "private"}
    assert [p.name for p in (boot.state / "private").iterdir()] == [stream]
    assert boot.owners.owners[_key(boot.state / "private" / stream)] == (SIGNER, SIGNER_GID)
    assert manifest["streams"][stream]["ledger_path"] == str(boot.projection)
    assert json.loads((boot.state / "manifest.json").read_bytes()) == manifest
    assert _head(boot, manifest)["seq"] == 0
    assert _boot_as_unit(boot) == manifest


def _key(path):
    info = os.lstat(path)
    return info.st_dev, info.st_ino


@pytest.mark.parametrize("cut", sorted(UNPUBLISHED_CUTS))
def test_cut_before_manifest_publication_clears_and_provisions_on_next_boot(boot, monkeypatch, cut):
    with monkeypatch.context() as patch:
        UNPUBLISHED_CUTS[cut](patch)
        with pytest.raises(PowerCut):
            _boot_as_unit(boot)
    assert any(boot.state.iterdir()) and not (boot.state / "manifest.json").exists()
    left = [p.name for p in (boot.state / "private").iterdir()] if (boot.state / "private").exists() else []
    _assert_fresh_stream(boot, _boot_as_unit(boot), left)
    assert not boot.projection.exists() and {p.name for p in boot.store.iterdir()} == {"exo"}


def _cut_before_link(boot, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, "link", _cut)
        with pytest.raises(PowerCut):
            _boot_as_unit(boot)
    return next((boot.state / "private").iterdir())


@pytest.mark.parametrize("cut", range(1, 18))
def test_cut_while_clearing_leaves_state_the_next_boot_clears(boot, monkeypatch, cut):
    """Each unlink or rmdir of the clearing, with SQLite side files present, is a cut point."""
    stream = _cut_before_link(boot, monkeypatch)
    temporary = next(boot.state.glob(".manifest-*"))
    reader = []  # a reader that outlives the boot check keeps the WAL side files in place
    _as_signer(boot, lambda: reader.append(sqlite3.connect(stream / "journal.sqlite")) or
               reader[0].execute("SELECT COUNT(*) FROM checkpoints").fetchone())
    assert {"journal.sqlite-wal", "journal.sqlite-shm"} <= {p.name for p in stream.iterdir()}
    try:
        attempted = []
        with monkeypatch.context() as patch:
            for name in ("unlink", "rmdir"):
                real = getattr(os, name)

                def call(path, *args, real=real, **kwargs):
                    attempted.append(os.fspath(path))
                    if len(attempted) == cut:
                        raise PowerCut()
                    return real(path, *args, **kwargs)
                patch.setattr(os, name, call)
            with pytest.raises(PowerCut):
                _boot_as_unit(boot)
        assert attempted[0] == temporary.name  # the temporary goes before the journal it requires
        cleared = [name for name in attempted if name.startswith("journal.sqlite")]
        assert len(cleared) < 3 or cleared[-1] == "journal.sqlite"  # side files go before their database
        _assert_fresh_stream(boot, _boot_as_unit(boot), [stream.name])
    finally:
        _as_signer(boot, lambda: reader[0].close())


def test_cut_after_manifest_link_keeps_the_published_stream(boot, monkeypatch):
    unlink = Path.unlink
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", lambda path, *a, **kw: _cut() if path.name.startswith(".manifest-")
                      else unlink(path, *a, **kw))
        with pytest.raises(PowerCut):
            _boot_as_unit(boot)
    published = json.loads((boot.state / "manifest.json").read_bytes())
    assert (boot.state / "manifest.json").stat().st_nlink == 2
    assert _boot_as_unit(boot) == published
    assert {p.name for p in boot.state.iterdir()} == {"manifest.json", "private"}
    assert (boot.state / "manifest.json").stat().st_nlink == 1


def test_established_boot_leaves_an_unverified_manifest_leftover_in_place(boot):
    manifest = _boot_as_unit(boot)
    stray = boot.state / ".manifest-0123456789abcdef"
    stray.write_bytes((boot.state / "manifest.json").read_bytes())
    stray.chmod(0o644)
    assert _boot_as_unit(boot) == manifest
    assert stray.exists() and (boot.state / "manifest.json").stat().st_nlink == 1


def _provisioned_then_lost(boot):
    manifest = _boot_as_unit(boot)
    (boot.state / "manifest.json").unlink()  # the root manifest is lost after publication
    return manifest


def _signed_event(boot, manifest):
    restored = boot.state / "manifest.json"
    restored.write_text(json.dumps(manifest))
    _append(boot, manifest)
    restored.unlink()


# Faults on state left without a manifest, and the boot refusal each one gives.
UNPROVEN = {
    "signed_event": "not provably unsigned",
    "head_above_genesis": "not provably unsigned",
    "unknown_table": "not provably unsigned",
    "projection": "fresh store must contain only",
    "store_entry": "fresh store has unexpected entries",
    "signer_entry": "unexpected entries",
    "second_stream": "unexpected entries",
    "unknown_stream_file": "stream has unexpected entries",
    "loose_key_mode": "wrong type, owner or mode",
    "symlinked_private": "wrong type, owner or mode",
    "linked_temporary": "unverified manifest leftover",
    "temporary_without_journal": "unverified manifest leftover",
    # Owners provisioning never leaves at any step.
    "root_owned_private_with_stream": "wrong type, owner or mode",
    "root_owned_stream_with_files": "wrong type, owner or mode",
    "root_owned_key": "wrong type, owner or mode",
    "signer_owned_temporary": "wrong type, owner or mode",
    "root_owned_stream_with_keys": "wrong type, owner or mode",
    "root_owned_private_with_keys": "wrong type, owner or mode",
    # Entry kinds provisioning never writes, and SQLite side files it never leaves without their database.
    "bad_stream_name": "unexpected entries",
    "symlinked_stream": "wrong type, owner or mode",
    "symlinked_key": "wrong type, owner or mode",
    "symlinked_temporary": "wrong type, owner or mode",
    "wal_without_journal": "stream has unexpected entries",
    "shm_without_journal": "stream has unexpected entries",
    "rollback_journal_without_journal": "stream has unexpected entries",
}


def _signed_frames_only(boot, manifest, directory, tmp_path):
    """Leave a signed append only in the WAL: the keys and journal.sqlite-wal stay, the database goes."""
    journal, wal = directory / "journal.sqlite", directory / "journal.sqlite-wal"
    reader = []  # an open reader keeps the WAL, and the frames of the append, in place
    _as_signer(boot, lambda: reader.append(sqlite3.connect(journal)) or
               reader[0].execute("SELECT COUNT(*) FROM checkpoints").fetchone())
    try:
        genesis = journal.read_bytes()
        _signed_event(boot, manifest)
        frames = wal.read_bytes()
    finally:
        _as_signer(boot, lambda: reader[0].close())
    proof = tmp_path / "proof"
    proof.mkdir()
    (proof / "journal.sqlite").write_bytes(genesis)
    (proof / "journal.sqlite-wal").write_bytes(frames)
    db = sqlite3.connect(proof / "journal.sqlite")
    try:
        assert db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1  # the WAL alone holds the event
    finally:
        db.close()

    def leave_frames():
        for name in ("journal.sqlite", "journal.sqlite-wal", "journal.sqlite-shm"):
            (directory / name).unlink(missing_ok=True)
        wal.write_bytes(frames)
    _as_signer(boot, leave_frames)


def _inject(boot, tmp_path, manifest, fault):
    stream = next(iter(manifest["streams"]))
    directory = boot.state / "private" / stream
    if fault == "signed_event":
        _signed_event(boot, manifest)
    elif fault == "head_above_genesis":
        _sql(boot, directory / "journal.sqlite", "INSERT INTO checkpoints VALUES (1, '{}')")
    elif fault == "unknown_table":
        _sql(boot, directory / "journal.sqlite", "CREATE TABLE notes (body TEXT)")
    elif fault == "projection":
        boot.projection.parent.mkdir(parents=True)
        boot.projection.write_bytes(b"")
    elif fault == "store_entry":
        (boot.store / "model-spend").mkdir()
    elif fault == "signer_entry":
        (boot.state / "notes").write_text("x")
    elif fault == "second_stream":
        (boot.state / "private" / ("s_" + "0" * 32)).mkdir(mode=0o700)
    elif fault == "unknown_stream_file":
        _as_signer(boot, lambda: (directory / "notes").write_text("x"))
    elif fault == "loose_key_mode":
        (directory / "checkpoint.key").chmod(0o644)
    elif fault == "symlinked_private":
        (boot.state / "private").rename(tmp_path / "moved-private")
        (boot.state / "private").symlink_to(tmp_path / "moved-private", target_is_directory=True)
    elif fault == "linked_temporary":
        temporary = boot.state / ".manifest-0123456789abcdef"
        temporary.write_text("{}")
        temporary.chmod(0o644)
        os.link(temporary, tmp_path / "other-link")
    elif fault == "temporary_without_journal":
        (boot.state / ".manifest-0123456789abcdef").write_text("{}")
        _as_signer(boot, lambda: (directory / "journal.sqlite").unlink())
    elif fault == "root_owned_private_with_stream":
        boot.owners.set(boot.state / "private", 0, 0)
    elif fault == "root_owned_stream_with_files":
        boot.owners.set(directory, 0, 0)
    elif fault == "root_owned_key":
        boot.owners.set(directory / "checkpoint.key", 0, 0)
    elif fault == "signer_owned_temporary":
        temporary = boot.state / ".manifest-0123456789abcdef"
        temporary.write_text("{}")
        temporary.chmod(0o644)
        boot.owners.set(temporary, SIGNER, SIGNER_GID)
    elif fault in ("root_owned_stream_with_keys", "root_owned_private_with_keys"):
        _as_signer(boot, lambda: (directory / "journal.sqlite").unlink())
        boot.owners.set(directory if fault == "root_owned_stream_with_keys" else boot.state / "private", 0, 0)
    elif fault == "bad_stream_name":
        (directory.parent / "notes").mkdir(mode=0o700)
    elif fault in ("symlinked_stream", "symlinked_key"):
        path = directory if fault == "symlinked_stream" else directory / "checkpoint.key"
        path.rename(tmp_path / "moved")
        path.symlink_to(tmp_path / "moved", target_is_directory=fault == "symlinked_stream")
    elif fault == "symlinked_temporary":
        (tmp_path / "moved").write_text("{}")
        (boot.state / ".manifest-0123456789abcdef").symlink_to(tmp_path / "moved")
    elif fault == "wal_without_journal":
        _signed_frames_only(boot, manifest, directory, tmp_path)
    elif fault in ("shm_without_journal", "rollback_journal_without_journal"):
        side = directory / ("journal.sqlite-shm" if fault == "shm_without_journal" else "journal.sqlite-journal")

        def leave_side_file():
            side.write_bytes((directory / "journal.sqlite").read_bytes())
            (directory / "journal.sqlite").unlink()
        _as_signer(boot, leave_side_file)
    else:
        raise AssertionError(fault)
    return directory


@pytest.mark.parametrize("fault", sorted(UNPROVEN))
def test_missing_manifest_refuses_state_it_cannot_prove_unpublished(boot, tmp_path, fault):
    manifest = _provisioned_then_lost(boot)
    directory = _inject(boot, tmp_path, manifest, fault)
    names = {p.name for p in boot.state.iterdir()}
    held = sorted(p.name for p in directory.iterdir()) if directory.exists() else None
    with pytest.raises(AuthorityError, match=UNPROVEN[fault] + ".*root can run exo-ledger-admin repair"):
        _boot_as_unit(boot)
    assert {p.name for p in boot.state.iterdir()} == names and not (boot.state / "manifest.json").exists()
    if held is not None and fault != "symlinked_private":
        side = {"journal.sqlite-wal", "journal.sqlite-shm"}  # SQLite may add or drop these while reading
        assert set(held) - side <= {p.name for p in directory.iterdir()} <= set(held) | side
    if fault == "signed_event":
        assert _count(boot, directory / "journal.sqlite", "records") == 1


def test_truncated_manifest_from_an_older_provision_refuses_and_is_never_reseeded(boot):
    _boot_as_unit(boot)
    path = boot.state / "manifest.json"
    truncated = path.read_bytes()[:57]
    path.write_bytes(truncated)
    with pytest.raises(AuthorityError, match="manifest is invalid.*published ledger is never reseeded"):
        _boot_as_unit(boot)
    before = _tree(boot.state)
    with pytest.raises(AuthorityError, match="repair refuses: a manifest was published.*reviewed migration"):
        admin.repair(state=boot.state, store=boot.store)
    assert _tree(boot.state) == before and path.read_bytes() == truncated


def test_provision_refuses_a_manifest_leftover(boot):
    (boot.state / ".manifest-0123456789abcdef").write_text("{}")
    with pytest.raises(AuthorityError, match="partial signer state"):
        admin.provision("exo", state=boot.state, uid=SIGNER, gid=SIGNER_GID, projection=boot.projection)


def test_repair_never_replaces_valid_state(boot):
    manifest = _boot_as_unit(boot)
    before = {p.name: p.stat().st_ino for p in boot.state.iterdir()}
    assert admin.repair(state=boot.state, store=boot.store) == {"preserved": [], "refusal": None,
                                                                "manifest": manifest}
    assert {p.name: p.stat().st_ino for p in boot.state.iterdir()} == before


# Refused state that may hold signed or published history, and why repair refuses it too.
PUBLISHED = {
    "signed_event": "not provably unsigned",
    "head_above_genesis": "not provably unsigned",
    "unknown_table": "not provably unsigned",
    "projection": "store is not the empty first-boot skeleton",
    "store_entry": "store is not the empty first-boot skeleton",
    "signer_entry": "cannot classify signer entry",
    "unknown_stream_file": "cannot classify signer stream",
    "symlinked_private": "cannot classify signer entry",
    "temporary_without_journal": "temporary manifest has no journal",
    "bad_stream_name": "cannot classify signer entry",
    "symlinked_stream": "cannot classify signer entry",
    "symlinked_key": "cannot classify signer entry",
    "symlinked_temporary": "cannot classify signer entry",
    "wal_without_journal": "cannot classify signer stream",  # the frames of a signed event
    "shm_without_journal": "cannot classify signer stream",
    "rollback_journal_without_journal": "cannot classify signer stream",
    # The signer UID cannot enter a root-owned 0700 directory on the journal's path.
    "root_owned_private_with_stream": "not provably unsigned",
    "root_owned_stream_with_files": "not provably unsigned",
}


@pytest.mark.parametrize("fault", sorted(PUBLISHED))
def test_repair_refuses_state_that_may_hold_history_and_changes_nothing(boot, tmp_path, fault):
    manifest = _provisioned_then_lost(boot)
    _inject(boot, tmp_path, manifest, fault)
    before, store = _tree(boot.state), _tree(boot.store)
    with pytest.raises(AuthorityError, match=f"repair refuses: .*{PUBLISHED[fault]}.*never reseeded"):
        admin.repair(state=boot.state, store=boot.store)
    assert _tree(boot.state) == before and _tree(boot.store) == store
    assert not (boot.state / admin.VAULT).exists()


def test_repair_refuses_a_published_manifest_that_fails_validation(boot, monkeypatch):
    manifest = _boot_as_unit(boot)
    stream = next(iter(manifest["streams"]))
    (boot.state / "private" / stream / "checkpoint.key").chmod(0o644)
    with pytest.raises(AuthorityError, match="published ledger is never reseeded"):
        _boot_as_unit(boot)
    before = _tree(boot.state)
    with pytest.raises(AuthorityError, match="repair refuses: a manifest was published"):
        admin.repair(state=boot.state, store=boot.store)
    assert _tree(boot.state) == before


# Unpublished state that boot will not delete, because no provisioning step leaves it. Each journal
# must still open under the signer UID, so a root-owned directory is here only when it holds none.
UNPUBLISHED_REPAIRS = ("loose_key_mode", "second_stream", "linked_temporary", "root_owned_key",
                       "signer_owned_temporary", "root_owned_stream_with_keys", "root_owned_private_with_keys")


@pytest.mark.parametrize("fault", UNPUBLISHED_REPAIRS)
def test_repair_preserves_unpublished_state_boot_will_not_clear(boot, tmp_path, fault):
    manifest = _provisioned_then_lost(boot)
    old = next(iter(manifest["streams"]))
    _inject(boot, tmp_path, manifest, fault)
    before = _tree(boot.state)
    with pytest.raises(AuthorityError, match=UNPROVEN[fault]):
        _boot_as_unit(boot)
    result = admin.repair(state=boot.state, store=boot.store)
    slot = Path(result["preserved"][0])
    assert slot.parent == boot.state / admin.VAULT and stat.S_IMODE(slot.stat().st_mode) == 0o700
    assert boot.owners.owners.get(_key(slot), (0, 0)) == (0, 0)
    assert UNPROVEN[fault] in result["refusal"]
    assert {name.split("/", 1)[0] for name in _tree(slot)} == {name.split("/", 1)[0] for name in before}
    assert (slot / "private" / old / "checkpoint.key").read_bytes() == before[f"private/{old}/checkpoint.key"]
    _assert_fresh_stream(boot, result["manifest"], [old])
    assert {p.name for p in boot.store.iterdir()} == {"exo"}


def test_repair_resumes_after_a_cut_during_its_own_provision(boot, monkeypatch, tmp_path):
    manifest = _provisioned_then_lost(boot)
    _inject(boot, tmp_path, manifest, "loose_key_mode")
    with monkeypatch.context() as patch:
        patch.setattr(admin.os, "link", _cut)
        with pytest.raises(PowerCut):
            admin.repair(state=boot.state, store=boot.store)
    with pytest.raises(AuthorityError, match="unexpected entries.*root can run exo-ledger-admin repair"):
        _boot_as_unit(boot)  # the earlier repair directory is not something provisioning writes
    result = admin.repair(state=boot.state, store=boot.store)
    assert len(list((boot.state / admin.VAULT).iterdir())) == 2
    _assert_fresh_stream(boot, result["manifest"])


@pytest.mark.parametrize("fault", ["loose", "signer_owned", "symlinked"])
def test_repair_refuses_a_repair_directory_it_cannot_trust(boot, monkeypatch, tmp_path, fault):
    manifest = _provisioned_then_lost(boot)
    _inject(boot, tmp_path, manifest, "loose_key_mode")
    with monkeypatch.context() as patch:
        patch.setattr(admin, "provision", _cut)
        with pytest.raises(PowerCut):
            admin.repair(state=boot.state, store=boot.store)
    vault = boot.state / admin.VAULT
    assert {p.name for p in boot.state.iterdir()} == {admin.VAULT}
    if fault == "loose":
        vault.chmod(0o755)
    elif fault == "signer_owned":
        boot.owners.set(vault, SIGNER, SIGNER_GID)
    else:
        vault.rename(tmp_path / "moved-repair")
        vault.symlink_to(tmp_path / "moved-repair", target_is_directory=True)
    before = _tree(boot.state)
    with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
        admin.repair(state=boot.state, store=boot.store)
    assert _tree(boot.state) == before and not (boot.state / "manifest.json").exists()


def test_repair_command_prints_its_result(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(admin, "repair", lambda principal: seen.append(principal) or {"preserved": []})
    monkeypatch.setattr(admin.sys, "argv", ["exo-ledger-admin", "repair"])
    admin.main()
    assert seen == ["exo"] and json.loads(capsys.readouterr().out) == {"preserved": []}
    monkeypatch.setattr(admin.sys, "argv", ["exo-ledger-admin", "repair", "--set-aside-projection"])
    with pytest.raises(SystemExit):
        admin.main()


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root Linux test container")
def test_unsigned_evidence_runs_under_the_signer_uid():
    import shutil
    import tempfile

    from exo import crypto
    from exo.ledger_authority.journal import AuthorityJournal

    account = pwd.getpwnam("nobody")
    uid, gid = account.pw_uid, account.pw_gid
    root = Path(tempfile.mkdtemp(prefix="exo-signer-", dir="/tmp"))
    os.chown(root, uid, gid)
    root.chmod(0o700)
    path = root / "journal.sqlite"
    key, checkpoint = crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "fixture", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {"exo-os": {"pub": crypto.pubkey_b64(key), "author": "exo-os", "authors": ["exo-os"]}}}
    try:
        admin._as_signer(uid, gid, lambda: AuthorityJournal.provision(path, identity, {"exo-os": key}, checkpoint))
        assert path.stat().st_uid == uid
        admin._as_signer(uid, gid, lambda: admin._unsigned(path))

        def append():
            journal = AuthorityJournal(path, identity, {"exo-os": key}, checkpoint)
            head = journal.head()
            journal.append("exo-os", "op-1", {"seq": head["seq"], "chain": head["chain"]},
                           {"author": "exo-os", "kind": "action", "body": {}, "session": "s", "refs": []})
        admin._as_signer(uid, gid, append)
        with pytest.raises(AuthorityError, match="signer validation or provisioning failed"):
            admin._as_signer(uid, gid, lambda: admin._unsigned(path))
    finally:
        shutil.rmtree(root)


def _real_signer(monkeypatch, account, prefix):
    import tempfile

    root = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))
    root.chmod(0o755)
    state, store = root / "signer", root / "store"
    state.mkdir(mode=0o755)
    store.mkdir(mode=0o755)
    (store / "exo").mkdir(mode=0o700)
    monkeypatch.setattr(admin, "validate_boot_mounts", lambda *a: None)
    monkeypatch.setattr(admin, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(policy, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(admin, "account_uid", lambda name: account.pw_uid)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: account)
    return root, state, store


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root Linux test container")
def test_step_owners_hold_with_a_real_signer_account(monkeypatch):
    """Real chown and a real UID drop: a cut resumes, and a root-owned stream holding keys refuses."""
    import shutil

    account = pwd.getpwnam("nobody")
    root, state, store = _real_signer(monkeypatch, account, "exo-first-boot-")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(admin, "validate_manifest", _cut)
            with pytest.raises(PowerCut):
                admin.ensure_boot(state=state, store=store)
        stream = next((state / "private").iterdir())
        assert stream.stat().st_uid == account.pw_uid and (stream / "journal.sqlite").stat().st_uid == account.pw_uid
        os.chown(stream, 0, 0)
        with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
            admin.ensure_boot(state=state, store=store)
        os.chown(stream, account.pw_uid, account.pw_gid)
        manifest = admin.ensure_boot(state=state, store=store)
        assert next(iter(manifest["streams"])) != stream.name and not stream.exists()
        assert admin.ensure_boot(state=state, store=store) == manifest
    finally:
        shutil.rmtree(root)


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root Linux test container")
@pytest.mark.parametrize("owned", ["private", "stream"])
def test_repair_refuses_a_journal_a_real_signer_account_cannot_reach(monkeypatch, owned):
    """A real UID drop cannot open this journal, as the host fake models, so repair refuses and moves nothing."""
    import shutil

    account = pwd.getpwnam("nobody")
    root, state, store = _real_signer(monkeypatch, account, "exo-signer-reach-")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(admin, "validate_manifest", _cut)
            with pytest.raises(PowerCut):
                admin.ensure_boot(state=state, store=store)
        stream = next((state / "private").iterdir())
        os.chown(state / "private" if owned == "private" else stream, 0, 0)
        before = _tree(state)
        with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
            admin.ensure_boot(state=state, store=store)
        with pytest.raises(AuthorityError, match="repair refuses: a signer journal is not provably unsigned"):
            admin.repair(state=state, store=store)
        assert _tree(state) == before
    finally:
        shutil.rmtree(root)

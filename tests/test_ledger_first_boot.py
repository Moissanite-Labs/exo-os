"""First-boot ledger provisioning cut at each step, refusals and root repair on a simulated signer mount."""
from __future__ import annotations

import json
import os
import pwd
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from exo.ledger_authority import AuthorityError

from exo_os.ledgerd import admin, policy, server


class PowerCut(Exception):
    """Nothing after the injected point runs, as after a power loss."""


def _cut(*args, **kwargs):
    raise PowerCut()


@pytest.fixture
def boot(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    state, store = root / "signer", root / "store"
    state.mkdir(mode=0o755)
    store.mkdir()
    (store / "exo").mkdir(mode=0o700)
    owner, gid = os.getuid(), os.getgid()
    signer = owner or 1000  # provisioning never accepts the root identity
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    monkeypatch.setattr(admin, "validate_boot_mounts", lambda *a: None)
    monkeypatch.setattr(admin, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(policy, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(admin, "account_uid", lambda name: signer)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_gid=gid))
    monkeypatch.setattr(admin.os, "chown", lambda *a: None)
    monkeypatch.setattr(admin, "_as_signer", lambda uid, gid, action: action())
    # Host tests own every file; model root and the signer account as that owner.
    inode = admin._inode
    monkeypatch.setattr(admin, "_inode", lambda path, kind, owners, forbidden=0o077: inode(
        path, kind, {owner if uid in (0, signer) else uid for uid in owners}, forbidden))
    load = server.load_journals
    monkeypatch.setattr(server, "load_journals", lambda manifest, uid, where=policy.STATE: load(manifest, owner, where))
    return SimpleNamespace(state=state, store=store, owner=owner,
                           projection=store / "exo/principals/exo/ledger.sqlite")


def _boot_as_unit(boot):
    """The provisioning unit runs with UMask=0077."""
    previous = os.umask(0o077)
    try:
        return admin.ensure_boot(state=boot.state, store=boot.store)
    finally:
        os.umask(previous)


def _journal(boot, manifest):
    return _load_journal(boot, manifest)[1]


def _load_journal(boot, manifest):
    stream = next(iter(manifest["streams"]))
    return stream, server.load_journals(manifest, boot.owner, boot.state)[stream]


def _append(boot, manifest):
    stream, journal = _load_journal(boot, manifest)
    head = journal.head()
    return journal.append("exo-os", "op-1", {"seq": head["seq"], "chain": head["chain"]},
                          {"author": "exo-os", "kind": "action", "body": {"step": 1}, "session": "s", "refs": []})


def _count(path, table):
    db = sqlite3.connect(path)
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        db.close()


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
    "after_stream_mkdir": lambda patch: patch.setattr(admin.os, "chown", _cut_on_call(lambda *a: None, 2)),
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


@pytest.mark.parametrize("cut", sorted(UNPUBLISHED_CUTS))
def test_cut_before_manifest_publication_clears_and_provisions_on_next_boot(boot, monkeypatch, cut):
    with monkeypatch.context() as patch:
        UNPUBLISHED_CUTS[cut](patch)
        with pytest.raises(PowerCut):
            _boot_as_unit(boot)
    assert any(boot.state.iterdir()) and not (boot.state / "manifest.json").exists()
    left = [p.name for p in (boot.state / "private").iterdir()] if (boot.state / "private").exists() else []
    manifest = _boot_as_unit(boot)
    stream = next(iter(manifest["streams"]))
    assert stream not in left
    assert {p.name for p in boot.state.iterdir()} == {"manifest.json", "private"}
    assert [p.name for p in (boot.state / "private").iterdir()] == [stream]
    assert manifest["streams"][stream]["ledger_path"] == str(boot.projection)
    assert json.loads((boot.state / "manifest.json").read_bytes()) == manifest
    assert _journal(boot, manifest).head()["seq"] == 0
    assert _boot_as_unit(boot) == manifest
    assert not boot.projection.exists() and {p.name for p in boot.store.iterdir()} == {"exo"}


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
}


@pytest.mark.parametrize("fault", sorted(UNPROVEN))
def test_missing_manifest_refuses_state_it_cannot_prove_unpublished(boot, tmp_path, fault):
    manifest = _provisioned_then_lost(boot)
    stream = next(iter(manifest["streams"]))
    directory = boot.state / "private" / stream
    if fault == "signed_event":
        restored = boot.state / "manifest.json"
        restored.write_text(json.dumps(manifest))
        _append(boot, manifest)
        restored.unlink()
    elif fault == "head_above_genesis":
        db = sqlite3.connect(directory / "journal.sqlite")
        db.execute("INSERT INTO checkpoints VALUES (1, '{}')")
        db.commit()
        db.close()
    elif fault == "unknown_table":
        db = sqlite3.connect(directory / "journal.sqlite")
        db.execute("CREATE TABLE notes (body TEXT)")
        db.commit()
        db.close()
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
        (directory / "notes").write_text("x")
    elif fault == "loose_key_mode":
        (directory / "checkpoint.key").chmod(0o644)
    elif fault == "symlinked_private":
        (boot.state / "private").rename(tmp_path / "moved-private")
        (boot.state / "private").symlink_to(tmp_path / "moved-private", target_is_directory=True)
    else:
        temporary = boot.state / ".manifest-0123456789abcdef"
        temporary.write_text("{}")
        os.link(temporary, tmp_path / "other-link")
    names = {p.name for p in boot.state.iterdir()}
    held = sorted(p.name for p in directory.iterdir()) if directory.exists() else None
    with pytest.raises(AuthorityError, match=UNPROVEN[fault] + ".*root repair: exo-ledger-admin repair"):
        _boot_as_unit(boot)
    assert {p.name for p in boot.state.iterdir()} == names and not (boot.state / "manifest.json").exists()
    if held is not None and fault != "symlinked_private":
        assert {"journal.sqlite", "checkpoint.key"} <= set(p.name for p in directory.iterdir()) <= set(held) | {
            "journal.sqlite-wal", "journal.sqlite-shm"}
    if fault == "signed_event":
        assert _count(directory / "journal.sqlite", "records") == 1


def test_truncated_manifest_from_an_older_provision_names_repair(boot):
    _boot_as_unit(boot)
    path = boot.state / "manifest.json"
    truncated = path.read_bytes()[:57]
    path.write_bytes(truncated)
    with pytest.raises(AuthorityError, match="manifest is invalid.*root repair: exo-ledger-admin repair"):
        _boot_as_unit(boot)
    result = admin.repair(state=boot.state, store=boot.store)
    slot = Path(result["preserved"][0])
    assert (slot / "manifest.json").read_bytes() == truncated and (slot / "private").is_dir()
    assert _boot_as_unit(boot) == result["manifest"]


def test_provision_refuses_a_manifest_leftover(boot):
    (boot.state / ".manifest-0123456789abcdef").write_text("{}")
    with pytest.raises(AuthorityError, match="partial signer state"):
        admin.provision("exo", state=boot.state, uid=boot.owner or 1000, gid=os.getgid(), projection=boot.projection)


def test_repair_never_replaces_valid_state(boot):
    manifest = _boot_as_unit(boot)
    before = {p.name: p.stat().st_ino for p in boot.state.iterdir()}
    assert admin.repair(state=boot.state, store=boot.store) == {"preserved": [], "refusal": None,
                                                                "manifest": manifest}
    assert {p.name: p.stat().st_ino for p in boot.state.iterdir()} == before


def test_repair_preserves_signed_history_and_starts_a_new_stream(boot):
    manifest = _boot_as_unit(boot)
    record = _append(boot, manifest)
    old = next(iter(manifest["streams"]))
    (boot.state / "manifest.json").unlink()
    with pytest.raises(AuthorityError, match="not provably unsigned"):
        _boot_as_unit(boot)
    result = admin.repair(state=boot.state, store=boot.store)
    slot = Path(result["preserved"][0])
    assert slot.parent == boot.state / "repair" and (slot.stat().st_mode & 0o777) == 0o700
    assert "not provably unsigned" in result["refusal"]
    assert _count(slot / "private" / old / "journal.sqlite", "records") == 1
    stream = next(iter(result["manifest"]["streams"]))
    assert stream != old and record["event"]["seq"] == 1
    assert _boot_as_unit(boot) == result["manifest"]
    assert _journal(boot, result["manifest"]).head()["seq"] == 0


def test_repair_moves_a_projection_only_when_asked(boot):
    manifest = _boot_as_unit(boot)
    boot.projection.parent.mkdir(parents=True)
    boot.projection.write_bytes(b"principal copy")
    boot.projection.with_name("ledger.sqlite-wal").write_bytes(b"wal")
    (boot.state / "manifest.json").unlink()
    with pytest.raises(AuthorityError, match="fresh store must contain only"):
        _boot_as_unit(boot)
    before = {p.name for p in boot.state.iterdir()}
    with pytest.raises(AuthorityError, match="--set-aside-projection"):
        admin.repair(state=boot.state, store=boot.store)
    assert {p.name for p in boot.state.iterdir()} == before and boot.projection.exists()
    assert not (boot.state / "repair").exists() and not (boot.store / "ledger-repair").exists()
    result = admin.repair(state=boot.state, store=boot.store, set_aside_projection=True)
    signer_slot, projection_slot = map(Path, result["preserved"])
    assert (signer_slot / "private" / next(iter(manifest["streams"]))).is_dir()
    assert projection_slot.parent == boot.store / "ledger-repair"
    assert (projection_slot / "ledger.sqlite").read_bytes() == b"principal copy"
    assert (projection_slot / "ledger.sqlite-wal").read_bytes() == b"wal"
    assert not boot.projection.exists() and not boot.projection.with_name("ledger.sqlite-wal").exists()
    assert _boot_as_unit(boot) == result["manifest"]


def test_repair_command_passes_the_projection_flag(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(admin, "repair", lambda principal, set_aside_projection: seen.append(
        (principal, set_aside_projection)) or {"preserved": []})
    monkeypatch.setattr(admin.sys, "argv", ["exo-ledger-admin", "repair", "--set-aside-projection"])
    admin.main()
    assert seen == [("exo", True)] and json.loads(capsys.readouterr().out) == {"preserved": []}


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

"""First-boot activation and fail-closed persistence on a simulated store."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from exo.core.models.spend import SpendDenied
from exo.ledger_authority import AuthorityError

from exo_os import spend


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    (root / "exo").mkdir(mode=0o700)
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(spend.os, "geteuid", lambda: 0)
    monkeypatch.setattr(spend.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=uid, pw_gid=gid))
    monkeypatch.setattr(spend, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(spend, "_ledger_ready", lambda *a: None)
    monkeypatch.setattr(spend.os, "chown", lambda *a: None)
    monkeypatch.setattr(spend, "_as_exo", lambda uid, gid, action, result=False: action())
    original = spend._node
    def local_owner(path, kind, mode, wanted_uid, wanted_gid):
        return original(path, kind, mode, uid if wanted_uid == 0 else wanted_uid,
                        gid if wanted_gid == 0 else wanted_gid)
    monkeypatch.setattr(spend, "_node", local_owner)
    return root


def test_fresh_zero_policy_is_published_last_and_never_reseeded(store):
    first = spend.ensure_boot(store=store)
    policy = store / "model-spend/policy.json"
    journal = store / "model-spend/exo/journal.sqlite"
    assert first == {"principal": "exo", "journal": str(journal), "journal_id": first["journal_id"],
                     "daily_usd": "0", "monthly_usd": "0", "routes": {}}
    assert stat.S_IMODE(policy.stat().st_mode) == 0o644
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    assert spend.ensure_boot(store=store) == first
    first["daily_usd"] = "12"
    policy.write_text(json.dumps(first))
    assert spend.ensure_boot(store=store)["daily_usd"] == "12"
    assert spend.inspect(store=store)["journal_id"] == first["journal_id"]


def test_missing_and_partial_state_never_auto_recreate(store):
    (store / ".owned").touch()
    with pytest.raises(AuthorityError, match="missing established"):
        spend.ensure_boot(store=store)
    (store / ".owned").unlink()
    (store / "model-spend").mkdir()
    with pytest.raises(AuthorityError, match="wrong type, owner or mode|unexpected entries"):
        spend.ensure_boot(store=store)
    assert not (store / "model-spend/policy.json").exists()


def test_missing_state_with_nonempty_principal_never_counts_as_fresh(store):
    (store / "exo" / "config.json").write_text("{}")
    with pytest.raises(AuthorityError, match="unexpected entries"):
        spend.ensure_boot(store=store)
    assert not (store / "model-spend").exists()


def test_failed_policy_publication_leaves_explicit_repair_state(store, monkeypatch):
    monkeypatch.setattr(spend.os, "link", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk fault")))
    with pytest.raises(OSError, match="disk fault"):
        spend.ensure_boot(store=store)
    state = store / "model-spend"
    assert (state / "exo/journal.sqlite").exists()
    assert not (state / "policy.json").exists()
    with pytest.raises(AuthorityError):
        spend.ensure_boot(store=store)


@pytest.mark.parametrize("fault", ["missing_policy", "corrupt_policy", "missing_journal", "symlink_journal",
                                   "symlink_policy", "symlink_state", "loose_policy", "loose_journal",
                                   "corrupt_sqlite", "wrong_identity"])
def test_established_corruption_refuses_without_reseed(store, fault):
    first = spend.ensure_boot(store=store)
    policy = store / "model-spend/policy.json"
    journal = store / "model-spend/exo/journal.sqlite"
    if fault == "missing_policy":
        policy.unlink()
    elif fault == "corrupt_policy":
        policy.write_text("{")
    elif fault == "missing_journal":
        journal.unlink()
    elif fault == "symlink_journal":
        journal.unlink()
        journal.symlink_to(store / "exo")
    elif fault == "symlink_policy":
        saved = store / "saved-policy.json"
        policy.rename(saved)
        policy.symlink_to(saved)
    elif fault == "symlink_state":
        state = store / "model-spend"
        state.rename(store / "saved-model-spend")
        state.symlink_to(store / "saved-model-spend", target_is_directory=True)
    elif fault == "loose_policy":
        policy.chmod(0o666)
    elif fault == "loose_journal":
        journal.chmod(0o644)
    elif fault == "corrupt_sqlite":
        journal.write_bytes(b"corrupt sqlite database")
    else:
        first["journal_id"] = "00000000-0000-0000-0000-000000000001"
        policy.write_text(json.dumps(first))
    with pytest.raises((AuthorityError, ValueError, SpendDenied, sqlite3.DatabaseError)):
        spend.ensure_boot(store=store)
    if fault in ("missing_journal", "symlink_journal"):
        assert not journal.is_file()


def test_strict_mode_and_symlink_rejection(tmp_path):
    path = tmp_path / "target"
    path.write_text("x")
    info = path.lstat()
    expected_uid, expected_gid = info.st_uid, info.st_gid
    spend._node(path, stat.S_IFREG, 0o644, expected_uid, expected_gid)
    path.chmod(0o666)
    with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
        spend._node(path, stat.S_IFREG, 0o644, expected_uid, expected_gid)
    path.unlink()
    path.symlink_to(tmp_path)
    with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
        spend._node(path, stat.S_IFREG, 0o644, expected_uid, expected_gid)
    path.unlink()
    path.write_text("x")
    path.chmod(0o644)
    with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
        spend._node(path, stat.S_IFREG, 0o644, expected_uid + 1, expected_gid)


def test_established_misowned_journal_refuses(store, monkeypatch):
    spend.ensure_boot(store=store)
    journal = store / "model-spend/exo/journal.sqlite"
    original = Path.lstat
    def substituted(path):
        info = original(path)
        if path == journal:
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info
    monkeypatch.setattr(Path, "lstat", substituted)
    with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
        spend.ensure_boot(store=store)


def test_boot_requires_preprovisioned_ledger_and_mount_identity(store, monkeypatch):
    monkeypatch.setattr(spend, "_ledger_ready", lambda *a: (_ for _ in ()).throw(AuthorityError("missing signer")))
    with pytest.raises(AuthorityError, match="missing signer"):
        spend.ensure_boot(store=store)
    assert {p.name for p in store.iterdir()} == {"exo"}


def test_unit_graph_requires_spend_before_login():
    image = Path(__file__).resolve().parents[1] / "image/mkosi.extra/usr/lib/systemd"
    unit = (image / "system/exo-model-spend-provision.service").read_text()
    assert "DefaultDependencies=no" in unit
    assert "RequiresMountsFor=/var/lib/exo /var/lib/exo-ledger" in unit
    assert "Requires=exo-ledger-provision.service" in unit
    assert "After=local-fs.target exo-ledger-provision.service" in unit
    for name in ("exo-os-firstboot.service", "greetd.service", "systemd-user-sessions.service"):
        assert name in unit
    for rel in ("system/exo-os-firstboot.service", "system/greetd.service.d/10-exo-ledger.conf",
                "system/systemd-user-sessions.service.d/10-exo-ledger.conf"):
        content = (image / rel).read_text()
        assert "Requires=exo-model-spend-provision.service" in content
        assert "After=exo-model-spend-provision.service" in content
    for rel in ("user/exod.service", "user/exo-os-setup.service"):
        content = (image / rel).read_text()
        assert "Environment=EXO_MODEL_SPEND_REQUIRED=1" in content
        assert "Environment=EXO_MODEL_SPEND_POLICY=/var/lib/exo/model-spend/policy.json" in content

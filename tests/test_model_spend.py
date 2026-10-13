"""First-boot activation and fail-closed persistence on a simulated store."""
from __future__ import annotations

import json
import os
import pwd
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from exo.ledger_authority import AuthorityError
from exo.model_spend import SpendDenied, SpendJournal

from exo_os import spend


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    (root / "exo").mkdir(mode=0o700)
    owner_uid, owner_gid = os.getuid(), os.getgid()
    uid = owner_uid or 1000
    gid = owner_gid or 1000
    monkeypatch.setattr(spend.os, "geteuid", lambda: 0)
    monkeypatch.setattr(spend.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=uid, pw_gid=gid))
    monkeypatch.setattr(spend, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(spend, "_ledger_ready", lambda *a: None)
    monkeypatch.setattr(spend.os, "chown", lambda *a: None)
    monkeypatch.setattr(spend, "_as_exo", lambda uid, gid, action, result=False: action())
    original = spend._node
    def local_owner(path, kind, mode, wanted_uid, wanted_gid):
        expected_uid = owner_uid if wanted_uid in (0, uid) else wanted_uid
        expected_gid = owner_gid if wanted_gid in (0, gid) else wanted_gid
        return original(path, kind, mode, expected_uid, expected_gid)
    monkeypatch.setattr(spend, "_node", local_owner)
    return root


def test_root_identity_is_never_accepted(store, monkeypatch):
    monkeypatch.setattr(spend.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=0, pw_gid=0))
    with pytest.raises(AuthorityError, match="cannot use the root identity"):
        spend.ensure_boot(store=store)


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root Linux test container")
def test_as_exo_drops_identity_and_creates_owned_file():
    account = pwd.getpwnam("nobody")
    root = Path(tempfile.mkdtemp(prefix="exo-spend-", dir="/tmp"))
    os.chown(root, account.pw_uid, account.pw_gid)
    root.chmod(0o700)
    target = root / "owned"
    try:
        def create():
            target.write_text("owned")
            return {"uid": os.geteuid(), "gid": os.getegid()}

        identity = spend._as_exo(account.pw_uid, account.pw_gid, create, result=True)
        info = target.stat()
        assert identity == {"uid": account.pw_uid, "gid": account.pw_gid}
        assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (
            account.pw_uid, account.pw_gid, 0o600)
    finally:
        shutil.rmtree(root)


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


def test_zero_cap_priced_native_route_refuses_before_mock_http_and_offline_remains_usable(store, monkeypatch):
    import asyncio

    import httpx
    from exo.core.models.fake import RuleFake
    from exo.core.models.openai_chat import OpenAIChatProvider
    from exo.core.models.router import Router
    from exo.core.types import Message

    monkeypatch.delenv("EXO_MODEL_SPEND_POLICY", raising=False)
    monkeypatch.delenv("EXO_MODEL_SPEND_REQUIRED", raising=False)
    raw = spend.ensure_boot(store=store)
    ref = "native:gpt-4.1-2025-04-14"
    priced = {**raw, "routes": {ref: {
        "contract": "text-tokens-v1", "transport": "openai", "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-2025-04-14", "input_bound": 1_047_576, "max_output": 100,
        "input": "1", "output": "10", "cache_read": "1", "cache_write": "1"}}}
    seen = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: seen.append(request)))
    router = Router(roles={"main": [ref]}, providers={"native": {
        "transport": "openai", "base_url": "https://api.openai.com/v1"}},
        catalog={}, env={}, client=client, spend_config=priced)
    assert type(router.credentials("native")[0].provider) is OpenAIChatProvider

    async def call():
        try:
            return [event async for event in router.stream("main", [Message.user("blocked")], [])]
        finally:
            await client.aclose()

    event = asyncio.run(call())[-1]
    assert event.kind == "error" and not event.retryable and not seen
    assert event.attempts == [{"route": ref, "outcome": "spend_refused"}]
    assert "day model-spend cap" in (event.error or "")
    assert SpendJournal.inspect(priced)["state"] == "ready"

    offline = Router(roles={"main": ["fake:scripted"]}, catalog={}, env={}, spend_config=raw,
                     instances={"fake": RuleFake({"reply": "still offline"})})
    async def offline_call():
        return [event async for event in offline.stream("main", [Message.user("hello")], [])]
    assert asyncio.run(offline_call())[-1].kind == "done"
    assert SpendJournal.inspect(raw)["state"] == "offline"
    with sqlite3.connect(raw["journal"]) as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


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


@pytest.mark.parametrize("failure", [SystemExit(0), KeyboardInterrupt()])
@pytest.mark.parametrize("result", [False, True])
def test_as_exo_baseexception_never_escapes_or_reports_success(monkeypatch, tmp_path, failure, result):
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(spend.os, name, lambda *args: None)
    parent = os.getpid()
    escaped = tmp_path / "escaped"
    def action():
        raise failure
    try:
        with pytest.raises(AuthorityError, match="validation or provisioning failed"):
            spend._as_exo(1000, 1000, action, result=result)
    except BaseException:
        if os.getpid() != parent:
            escaped.write_text("child resumed caller")
            os._exit(0)
        raise
    assert not escaped.exists()


def test_published_policy_link_survives_interrupted_cleanup(store, monkeypatch):
    unlink = Path.unlink
    def fail(path, *args, **kwargs):
        if path.name.startswith(".policy-"):
            raise OSError("post-publication cleanup interrupted")
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError, match="cleanup interrupted"):
            spend.ensure_boot(store=store)
    state = store / "model-spend"
    policy = state / "policy.json"
    before = policy.read_bytes()
    assert policy.stat().st_nlink == 2
    repaired = spend.ensure_boot(store=store)
    assert policy.read_bytes() == before
    assert repaired["journal_id"] == json.loads(before)["journal_id"]
    assert policy.stat().st_nlink == 1
    assert {p.name for p in state.iterdir()} == {"policy.json", "exo"}


@pytest.mark.parametrize("fault", ["wrong_inode", "wrong_inode_two_links", "owner", "group", "mode", "symlink",
                                   "nlink", "extra", "bad_name", "too_long", "missing_policy"])
def test_policy_cleanup_rejects_unverified_leftovers(store, monkeypatch, fault):
    raw = spend.ensure_boot(store=store)
    state = store / "model-spend"
    policy = state / "policy.json"
    leftover = state / ".policy-0123456789abcdef"
    if fault in ("wrong_inode", "wrong_inode_two_links"):
        leftover.write_bytes(policy.read_bytes())
        leftover.chmod(0o644)
        if fault == "wrong_inode_two_links":
            os.link(policy, store / "policy-other")
            os.link(leftover, store / "temporary-other")
    elif fault == "symlink":
        leftover.symlink_to(policy)
    else:
        os.link(policy, leftover)
        if fault == "mode":
            leftover.chmod(0o600)
        elif fault == "nlink":
            os.link(policy, store / "third-link")
        elif fault == "extra":
            os.link(policy, state / ".policy-fedcba9876543210")
        elif fault == "bad_name":
            leftover.rename(state / ".policy-0123456789abcdeF")
        elif fault == "too_long":
            leftover.rename(state / (".policy-" + "0" * 17))
        elif fault == "missing_policy":
            policy.unlink()
        elif fault in ("owner", "group"):
            original = Path.lstat
            def wrong_owner(path):
                info = original(path)
                if path == leftover:
                    values = list(info)
                    values[4 if fault == "owner" else 5] += 1
                    return os.stat_result(values)
                return info
            monkeypatch.setattr(Path, "lstat", wrong_owner)
    before = {p.name for p in state.iterdir()}
    with pytest.raises(AuthorityError):
        spend.ensure_boot(store=store)
    assert {p.name for p in state.iterdir()} == before
    if fault == "missing_policy":
        assert not policy.exists()
    else:
        assert json.loads(policy.read_bytes())["journal_id"] == raw["journal_id"]


def test_status_accepts_verified_publication_link_without_removing_or_syncing(store, monkeypatch):
    raw = spend.ensure_boot(store=store)
    policy = store / "model-spend/policy.json"
    leftover = policy.with_name(".policy-0123456789abcdef")
    os.link(policy, leftover)
    before = policy.read_bytes()
    monkeypatch.setattr(spend, "_fsync", lambda *args: pytest.fail("inspection must not fsync"))
    assert spend.inspect(store=store)["journal_id"] == raw["journal_id"]
    assert leftover.exists() and policy.stat().st_nlink == 2
    assert policy.read_bytes() == before


def test_cleanup_waits_for_valid_original_journal(store):
    spend.ensure_boot(store=store)
    policy = store / "model-spend/policy.json"
    leftover = policy.with_name(".policy-0123456789abcdef")
    os.link(policy, leftover)
    (store / "model-spend/exo/journal.sqlite").write_bytes(b"corrupt")
    with pytest.raises((SpendDenied, sqlite3.DatabaseError)):
        spend.ensure_boot(store=store)
    assert leftover.exists()


@pytest.mark.parametrize("fault", ["drop", "serialize", "oversized", "write"])
def test_as_exo_refuses_child_setup_and_output_failures(monkeypatch, tmp_path, fault):
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(spend.os, name, lambda *args: None)
    if fault == "drop":
        monkeypatch.setattr(spend.os, "setuid", lambda *args: (_ for _ in ()).throw(OSError("drop failed")))
    if fault == "write":
        monkeypatch.setattr(spend.os, "write", lambda *args: (_ for _ in ()).throw(OSError("pipe failed")))
    called = tmp_path / "called"
    def action():
        called.touch()
        return object() if fault == "serialize" else "x" * 65536 if fault == "oversized" else {}
    with pytest.raises(AuthorityError, match="validation or provisioning failed"):
        spend._as_exo(1000, 1000, action, result=True)
    assert called.exists() == (fault != "drop")

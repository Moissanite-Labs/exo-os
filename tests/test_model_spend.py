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

from fake_owners import FakeOwners

from exo_os import spend


EXO_UID, EXO_GID = 4301, 4302  # neither root nor the test user, so every owner check runs


@pytest.fixture
def owners(tmp_path, monkeypatch):
    return FakeOwners(tmp_path, monkeypatch)


@pytest.fixture
def store(tmp_path, monkeypatch, owners):
    root = tmp_path.resolve() / "store"
    root.mkdir()
    (root / "exo").mkdir(mode=0o700)
    monkeypatch.setattr(spend.os, "geteuid", lambda: 0)
    monkeypatch.setattr(spend.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=EXO_UID, pw_gid=EXO_GID))
    monkeypatch.setattr(spend, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(spend, "_ledger_ready", lambda *a: None)

    # Root owns what root creates; exo owns what appears under its UID drop.
    def as_exo(uid, gid, action, result=False):
        value = []
        owners.run_as(uid, gid, lambda: value.append(json.loads(json.dumps(action())) if result else action()))
        return value[0] if result else None
    monkeypatch.setattr(spend, "_as_exo", as_exo)
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


def test_missing_and_partial_state_after_ownership_never_auto_recreate(store):
    (store / ".owned").touch()
    with pytest.raises(AuthorityError, match="missing established.*root can run exo-model-spend-admin repair"):
        spend.ensure_boot(store=store)
    (store / "model-spend").mkdir(mode=0o755)
    with pytest.raises(AuthorityError, match="missing after first boot completed.*exo-model-spend-admin repair"):
        spend.ensure_boot(store=store)
    assert not (store / "model-spend/policy.json").exists()
    assert not any((store / "model-spend").iterdir())


def test_missing_state_with_nonempty_principal_never_counts_as_fresh(store):
    (store / "exo" / "config.json").write_text("{}")
    with pytest.raises(AuthorityError, match="unexpected entries"):
        spend.ensure_boot(store=store)
    assert not (store / "model-spend").exists()


def test_failed_policy_publication_resumes_with_the_same_journal(store, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(spend.os, "link", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk fault")))
        with pytest.raises(OSError, match="disk fault"):
            spend.ensure_boot(store=store)
    state = store / "model-spend"
    assert (state / "exo/journal.sqlite").exists()
    assert not (state / "policy.json").exists()
    identity = _journal_identity(state / "exo/journal.sqlite")
    raw = spend.ensure_boot(store=store)
    assert raw["journal_id"] == identity and raw["daily_usd"] == raw["monthly_usd"] == "0" and raw["routes"] == {}
    assert {p.name for p in state.iterdir()} == {"policy.json", "exo"}


@pytest.mark.parametrize("fault", ["missing_policy", "corrupt_policy", "missing_journal", "symlink_journal",
                                   "symlink_policy", "symlink_state", "loose_policy", "loose_journal",
                                   "corrupt_sqlite", "wrong_identity"])
def test_established_corruption_refuses_without_reseed(store, fault):
    first = spend.ensure_boot(store=store)
    (store / ".owned").touch()  # firstboot finished: the policy was published and used
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
    (store / ".owned").touch()
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


class PowerCut(Exception):
    """Nothing after the injected point runs, as after a power loss."""


def _cut(*args, **kwargs):
    raise PowerCut()


def _journal_identity(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT id FROM identity").fetchone()[0]
    finally:
        db.close()


def _journal_write(path, statement, *values):
    db = sqlite3.connect(path)
    try:
        db.execute(statement, values)
        db.commit()
    finally:
        db.close()


def _tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def _boot_as_unit(store):
    """The provisioning unit runs with UMask=0077."""
    previous = os.umask(0o077)
    try:
        return spend.ensure_boot(store=store)
    finally:
        os.umask(previous)


def _empty_journal(path, principal):
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    raise PowerCut()


def _identity_table_only(path, principal):
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE identity (id TEXT NOT NULL, principal TEXT NOT NULL, blocked TEXT NOT NULL, "
               "observed REAL NOT NULL)")
    db.close()
    raise PowerCut()


def _uncommitted_identity(path, principal):
    """Schema committed, identity insert cut before COMMIT, hot rollback journal left behind."""
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript("CREATE TABLE identity (id TEXT NOT NULL, principal TEXT NOT NULL, blocked TEXT NOT NULL, "
                     "observed REAL NOT NULL); CREATE TABLE calls (id TEXT PRIMARY KEY, day TEXT NOT NULL, "
                     "month TEXT NOT NULL, route TEXT NOT NULL, session TEXT NOT NULL, reserved INTEGER NOT NULL, "
                     "actual INTEGER, pricing TEXT NOT NULL, completed_day TEXT, completed_month TEXT);")
    db.execute("BEGIN")
    db.execute("INSERT INTO identity VALUES ('00000000-0000-4000-8000-000000000001', ?, '', 0)", (principal,))
    hot = path.with_name(path.name + "-journal")
    saved = hot.read_bytes()
    db.close()  # rolls back and removes the journal; restore it as a power cut would leave it
    fd = os.open(hot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(saved)
    raise PowerCut()


def _torn_fdopen(real):
    def opener(fd, *args, **kwargs):
        handle = real(fd, *args, **kwargs)

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


# Each power-cut point in first-boot provisioning, and whether a journal identity was committed.
FIRST_BOOT_CUTS = {
    "after_state_mkdir": (lambda patch: patch.setattr(spend.os, "chmod", _cut), False),
    "after_journal_dir_mkdir": (lambda patch: patch.setattr(spend.os, "chown", _cut), False),
    "before_journal_seed": (lambda patch: patch.setattr(spend, "_as_exo", _cut), False),
    "empty_journal_file": (lambda patch: patch.setattr(spend.SpendJournal, "provision", _empty_journal), False),
    "partial_journal_schema": (lambda patch: patch.setattr(spend.SpendJournal, "provision",
                                                           _identity_table_only), False),
    "uncommitted_journal_identity": (lambda patch: patch.setattr(spend.SpendJournal, "provision",
                                                                 _uncommitted_identity), False),
    "after_journal_seed": (lambda patch: patch.setattr(spend, "validate_policy", _cut), True),
    "torn_temporary_policy": (lambda patch: patch.setattr(spend.os, "fdopen", _torn_fdopen(os.fdopen)), True),
    "temporary_policy_before_chmod": (lambda patch: patch.setattr(
        spend.os, "chmod", _cut_paths(os.chmod, ".policy-")), True),
    "temporary_policy_before_link": (lambda patch: patch.setattr(spend.os, "link", _cut), True),
}


@pytest.mark.parametrize("cut", sorted(FIRST_BOOT_CUTS))
def test_first_boot_cut_before_policy_publication_resumes_on_next_boot(store, monkeypatch, cut):
    inject, committed = FIRST_BOOT_CUTS[cut]
    state, journal = store / "model-spend", store / "model-spend/exo/journal.sqlite"
    with monkeypatch.context() as patch:
        inject(patch)
        with pytest.raises(PowerCut):
            _boot_as_unit(store)
    assert state.is_dir() and not (state / "policy.json").exists()
    identity = _journal_identity(journal) if committed else None
    if cut == "uncommitted_journal_identity":
        assert {p.name for p in journal.parent.iterdir()} == {"journal.sqlite", "journal.sqlite-journal"}
    raw = _boot_as_unit(store)
    assert raw == {"principal": "exo", "journal": str(journal), "journal_id": raw["journal_id"],
                   "daily_usd": "0", "monthly_usd": "0", "routes": {}}
    if committed:
        assert raw["journal_id"] == identity
    else:
        assert raw["journal_id"] != "00000000-0000-4000-8000-000000000001"
    assert _journal_identity(journal) == raw["journal_id"]
    assert {p.name for p in state.iterdir()} == {"policy.json", "exo"}
    assert {p.name for p in journal.parent.iterdir()} == {"journal.sqlite"}
    assert stat.S_IMODE(state.stat().st_mode) == 0o755
    assert json.loads((state / "policy.json").read_bytes()) == raw
    assert _boot_as_unit(store) == raw


def _cut_before_link(store, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(spend.os, "link", _cut)
        with pytest.raises(PowerCut):
            spend.ensure_boot(store=store)
    state = store / "model-spend"
    return state, state / "exo/journal.sqlite", next(state.glob(".policy-*"))


# Faults on state a cut-off first boot left, and the boot refusal each one gives.
UNPROVEN = {"one_call": "holds calls", "stopped": "used or bound elsewhere", "observed_clock": "used or bound",
            "other_principal": "used or bound elsewhere", "extra_table": "unexpected schema",
            "owned": "missing after first boot completed", "extra_store_entry": "unexpected entries",
            "extra_state_entry": "unexpected entries", "linked_temporary": "unexpected hard links",
            "temporary_without_journal": "no provisioned journal", "unsafe_journal_mode": "wrong type, owner or mode",
            # Owners first boot never leaves at any step.
            "root_owned_journal_dir_with_journal": "wrong type, owner or mode",
            "root_owned_journal": "wrong type, owner or mode",
            "exo_owned_state": "wrong type, owner or mode",
            "exo_owned_temporary": "wrong type, owner or mode",
            "state_mode_before_chmod_with_entries": "wrong type, owner or mode"}


def _inject(store, owners, tmp_path, state, journal, temporary, fault):
    if fault == "one_call":
        _journal_write(journal, "INSERT INTO calls (id, day, month, route, session, reserved, actual, pricing) "
                       "VALUES ('c1', '2026-10-20', '2026-10', 'native:m', 's', 1, NULL, '{}')")
    elif fault == "stopped":
        _journal_write(journal, "UPDATE identity SET blocked='uncertain charge'")
    elif fault == "observed_clock":
        _journal_write(journal, "UPDATE identity SET observed=1")
    elif fault == "other_principal":
        _journal_write(journal, "UPDATE identity SET principal='other'")
    elif fault == "extra_table":
        _journal_write(journal, "CREATE TABLE notes (body TEXT)")
    elif fault == "owned":
        (store / ".owned").touch()
    elif fault == "extra_store_entry":
        (store / "witness-publisher").mkdir()
    elif fault == "extra_state_entry":
        (state / "notes").write_text("x")
    elif fault == "linked_temporary":
        os.link(temporary, tmp_path / "other-link")
    elif fault == "temporary_without_journal":
        journal.unlink()
    elif fault == "unsafe_journal_mode":
        journal.chmod(0o644)
    elif fault == "root_owned_journal_dir_with_journal":
        owners.set(journal.parent, 0, 0)
    elif fault == "root_owned_journal":
        owners.set(journal, 0, 0)
    elif fault == "exo_owned_state":
        owners.set(state, EXO_UID, EXO_GID)
    elif fault == "exo_owned_temporary":
        owners.set(temporary, EXO_UID, EXO_GID)
    elif fault == "state_mode_before_chmod_with_entries":
        state.chmod(0o700)  # first boot sets 0755 before it creates anything inside
    else:
        raise AssertionError(fault)


@pytest.mark.parametrize("fault", sorted(UNPROVEN))
def test_interrupted_first_boot_refuses_state_it_cannot_prove_unpublished(store, owners, monkeypatch, tmp_path,
                                                                          fault):
    state, journal, temporary = _cut_before_link(store, monkeypatch)
    _inject(store, owners, tmp_path, state, journal, temporary, fault)
    before = _tree(state)
    with pytest.raises(AuthorityError, match=UNPROVEN[fault] + ".*root can run exo-model-spend-admin repair"):
        spend.ensure_boot(store=store)
    assert _tree(state) == before
    assert not (state / "policy.json").exists()


def test_repair_preserves_a_refused_journal_and_publishes_fresh_zero_caps(store, monkeypatch):
    state, journal, _ = _cut_before_link(store, monkeypatch)
    _journal_write(journal, "INSERT INTO calls (id, day, month, route, session, reserved, actual, pricing) "
                   "VALUES ('c1', '2026-10-20', '2026-10', 'native:m', 's', 1, NULL, '{}')")
    old = _journal_identity(journal)
    with pytest.raises(AuthorityError, match="holds calls"):
        spend.ensure_boot(store=store)
    result = spend.repair(store=store)
    preserved = Path(result["preserved"])
    assert preserved.name == "model-spend" and preserved.parent.parent == store / "model-spend-repair"
    for path in (preserved.parent, preserved.parent.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert "holds calls" in result["refusal"]
    assert _journal_identity(preserved / "exo/journal.sqlite") == old
    with sqlite3.connect(preserved / "exo/journal.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
    policy = result["policy"]
    assert policy["journal_id"] != old and policy["daily_usd"] == policy["monthly_usd"] == "0"
    assert policy["routes"] == {}
    assert spend.ensure_boot(store=store) == policy


def test_repair_provisions_missing_state_after_ownership(store):
    (store / ".owned").touch()
    with pytest.raises(AuthorityError, match="missing established"):
        spend.ensure_boot(store=store)
    result = spend.repair(store=store)
    assert result["preserved"] is None and "missing established" in result["refusal"]
    assert result["policy"]["daily_usd"] == "0" and result["policy"]["routes"] == {}
    assert spend.ensure_boot(store=store) == result["policy"]


# Unused state that boot will not delete or publish from, because no first-boot step leaves it.
UNUSED_REPAIRS = ("linked_temporary", "unsafe_journal_mode",
                  "root_owned_journal_dir_with_journal", "exo_owned_state", "exo_owned_temporary",
                  "state_mode_before_chmod_with_entries")


@pytest.mark.parametrize("fault", UNUSED_REPAIRS)
def test_repair_preserves_unused_state_boot_will_not_resume(store, owners, monkeypatch, tmp_path, fault):
    state, journal, temporary = _cut_before_link(store, monkeypatch)
    _inject(store, owners, tmp_path, state, journal, temporary, fault)
    old = _journal_identity(journal)
    before = _tree(state)
    with pytest.raises(AuthorityError, match=UNPROVEN[fault]):
        spend.ensure_boot(store=store)
    result = spend.repair(store=store)
    preserved = Path(result["preserved"])
    assert preserved.name == "model-spend" and preserved.parent.parent == store / "model-spend-repair"
    for path in (preserved.parent, preserved.parent.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert UNPROVEN[fault] in result["refusal"]
    assert _tree(preserved) == before
    policy = result["policy"]
    assert policy["journal_id"] != old and policy["daily_usd"] == policy["monthly_usd"] == "0"
    assert policy["routes"] == {}
    assert _journal_identity(state / "exo/journal.sqlite") == policy["journal_id"]
    assert spend.ensure_boot(store=store) == policy
    assert {p.name for p in store.iterdir()} == {"exo", "model-spend", "model-spend-repair"}


def test_repair_resumes_after_a_cut_during_its_own_provision(store, owners, monkeypatch, tmp_path):
    state, journal, temporary = _cut_before_link(store, monkeypatch)
    journal.chmod(0o644)
    with monkeypatch.context() as patch:
        patch.setattr(spend.os, "link", _cut)
        with pytest.raises(PowerCut):
            spend.repair(store=store)
    with pytest.raises(AuthorityError, match="unexpected entries.*root can run exo-model-spend-admin repair"):
        spend.ensure_boot(store=store)  # the earlier repair directory is not something first boot writes
    result = spend.repair(store=store)
    assert len(list((store / "model-spend-repair").iterdir())) == 2
    assert spend.ensure_boot(store=store) == result["policy"]


def test_repair_never_replaces_valid_state_or_clears_a_stop(store, owners):
    raw = spend.ensure_boot(store=store)
    (store / ".owned").touch()
    before = _tree(store)
    assert spend.repair(store=store) == {"preserved": None, "refusal": None, "policy": raw}
    assert _tree(store) == before
    owners.run_as(EXO_UID, EXO_GID, lambda: SpendJournal(raw).freeze("uncertain charge"))
    assert spend.repair(store=store)["preserved"] is None
    assert SpendJournal.inspect(raw)["state"] == "stopped"
    assert not (store / "model-spend-repair").exists()


def test_repair_requires_the_ledger_and_moves_nothing_without_it(store, monkeypatch):
    state, journal, _ = _cut_before_link(store, monkeypatch)
    journal.chmod(0o644)
    before = _tree(store)
    monkeypatch.setattr(spend, "_ledger_ready", lambda *a: (_ for _ in ()).throw(AuthorityError("missing signer")))
    with pytest.raises(AuthorityError, match="missing signer"):
        spend.repair(store=store)
    assert _tree(store) == before


def _persisted_rollback_journal(path, principal):
    """Schema committed in persistent journal mode, no identity: the rollback journal stays beside it."""
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("PRAGMA journal_mode=PERSIST")
    db.executescript("CREATE TABLE identity (id TEXT NOT NULL, principal TEXT NOT NULL, blocked TEXT NOT NULL, "
                     "observed REAL NOT NULL); CREATE TABLE calls (id TEXT PRIMARY KEY);")
    db.close()
    raise PowerCut()


@pytest.mark.parametrize("cut", range(1, 5))
def test_cut_while_clearing_an_unbound_journal_leaves_state_the_next_boot_clears(store, monkeypatch, cut):
    """Each unlink or rmdir of the clearing, with the rollback journal present, is a cut point."""
    state, journal = store / "model-spend", store / "model-spend/exo/journal.sqlite"
    with monkeypatch.context() as patch:
        patch.setattr(spend.SpendJournal, "provision", _persisted_rollback_journal)
        with pytest.raises(PowerCut):
            _boot_as_unit(store)
    assert {p.name for p in journal.parent.iterdir()} == {"journal.sqlite", "journal.sqlite-journal"}
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
            _boot_as_unit(store)
    assert attempted[0] == "journal.sqlite-journal"  # never left without its database
    raw = _boot_as_unit(store)
    assert _journal_identity(journal) == raw["journal_id"] and raw["daily_usd"] == "0"
    assert {p.name for p in state.iterdir()} == {"policy.json", "exo"}
    assert {p.name for p in journal.parent.iterdir()} == {"journal.sqlite"}


def test_repair_command_prints_its_result(monkeypatch, capsys):
    monkeypatch.setattr(spend, "repair", lambda: {"preserved": None, "refusal": None, "policy": {}})
    monkeypatch.setattr(spend.sys, "argv", ["exo-model-spend-admin", "repair"])
    spend.main()
    assert json.loads(capsys.readouterr().out) == {"policy": {}, "preserved": None, "refusal": None}


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root Linux test container")
def test_unpublished_journal_evidence_crosses_the_exo_uid_boundary():
    account = pwd.getpwnam("nobody")
    uid, gid = account.pw_uid, account.pw_gid
    root = Path(tempfile.mkdtemp(prefix="exo-spend-", dir="/tmp"))
    os.chown(root, uid, gid)
    root.chmod(0o700)
    empty, seeded = root / "empty.sqlite", root / "journal.sqlite"
    try:
        spend._as_exo(uid, gid, lambda: os.close(os.open(empty, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)))
        assert spend._as_exo(uid, gid, lambda: spend._unpublished_journal(empty, "exo"), result=True) is None
        identity = spend._as_exo(uid, gid, lambda: SpendJournal.provision(seeded, "exo"), result=True)
        assert spend._as_exo(uid, gid, lambda: spend._unpublished_journal(seeded, "exo"), result=True) == identity
        _journal_write(seeded, "INSERT INTO calls (id, day, month, route, session, reserved, actual, pricing) "
                       "VALUES ('c1', '2026-10-20', '2026-10', 'native:m', 's', 1, NULL, '{}')")
        with pytest.raises(AuthorityError, match="validation or provisioning failed"):
            spend._as_exo(uid, gid, lambda: spend._unpublished_journal(seeded, "exo"), result=True)
    finally:
        shutil.rmtree(root)


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable root Linux test container")
def test_step_owners_hold_with_a_real_exo_account(monkeypatch):
    """Real chown and a real UID drop: a cut resumes, and a root-owned journal directory refuses."""
    account = pwd.getpwnam("nobody")
    root = Path(tempfile.mkdtemp(prefix="exo-spend-boot-", dir="/tmp"))
    root.chmod(0o755)
    store = root / "store"
    store.mkdir(mode=0o755)
    (store / "exo").mkdir(mode=0o700)
    monkeypatch.setattr(spend, "trusted_path", lambda path, **kw: path)
    monkeypatch.setattr(spend, "_ledger_ready", lambda *a: None)
    monkeypatch.setattr(spend.pwd, "getpwnam", lambda _: account)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(spend, "validate_policy", _cut)
            with pytest.raises(PowerCut):
                _boot_as_unit(store)
        user_dir = store / "model-spend/exo"
        assert (user_dir / "journal.sqlite").stat().st_uid == account.pw_uid
        os.chown(user_dir, 0, 0)
        with pytest.raises(AuthorityError, match="wrong type, owner or mode"):
            _boot_as_unit(store)
        os.chown(user_dir, account.pw_uid, account.pw_gid)
        raw = _boot_as_unit(store)
        assert raw["daily_usd"] == "0" and spend.ensure_boot(store=store) == raw
    finally:
        shutil.rmtree(root)

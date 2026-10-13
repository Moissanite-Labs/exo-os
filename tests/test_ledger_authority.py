"""Portable protocol/policy, signer refusal and setup-order checks; fixture state only."""
import copy
from pathlib import Path

import pytest

from exo import crypto
from exo.ledger_authority import AuthorityError, AppendOutcomeUnknown, HeadConflict, verify_record
from exo.ledger_authority.journal import AuthorityJournal
from exo_os.ledgerd import client, policy, wire


@pytest.fixture
def stream(tmp_path):
    key, checkpoint = crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "fixture", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {"exo-os": {"pub": crypto.pubkey_b64(key), "author": "exo-os", "authors": ["exo-os"]}}}
    path = tmp_path / "journal.sqlite"
    journal = AuthorityJournal.provision(path, identity, {"exo-os": key}, checkpoint)
    manifest = {"version": 1, "streams": {"fixture": {"ledger_path": str(tmp_path / "projection.sqlite"),
                "identity": identity, "callers": {"exo-os": ["exo"]}}}}
    return journal, manifest, key, checkpoint


def intent():
    return {"author": "exo-os", "kind": "action", "body": {"phase": "fixture"}, "session": "s", "refs": []}


def test_journal_commits_once_and_reopens_without_reseeding(stream):
    journal, manifest, key, checkpoint = stream
    before = journal.head()
    expected = {k: before[k] for k in ("seq", "chain")}
    record = journal.append("exo-os", "operation", expected, intent())
    assert journal.append("exo-os", "operation", expected, intent()) == record
    verify_record(journal.identity, record, before)
    reopened = AuthorityJournal(journal.path, journal.identity, {"exo-os": key}, checkpoint)
    assert reopened.lookup("operation", writer="exo-os") == record and reopened.head()["seq"] == 1
    with pytest.raises((AuthorityError, FileExistsError)):
        AuthorityJournal.provision(journal.path, journal.identity, {"exo-os": key}, checkpoint)


def test_missing_established_journal_refused(stream):
    journal, _, key, checkpoint = stream
    journal.path.unlink()
    with pytest.raises(AuthorityError):
        AuthorityJournal(journal.path, journal.identity, {"exo-os": key}, checkpoint)
    assert not journal.path.exists()


def test_pinned_manifest_binds_exact_projection_and_writer(stream, monkeypatch, tmp_path):
    _, manifest, _, _ = stream
    assert policy.validate_manifest(manifest) == manifest
    monkeypatch.setattr(client, "load_manifest", lambda: manifest)
    entry = manifest["streams"]["fixture"]
    bound = client.create_authority(Path(entry["ledger_path"]), "exo-os")
    assert bound.identity == entry["identity"] and bound.writer == "exo-os"
    for path, writer in ((tmp_path / "elsewhere", "exo-os"), (Path(entry["ledger_path"]), "unknown")):
        with pytest.raises(AuthorityError):
            client.create_authority(path, writer)
    bad = copy.deepcopy(manifest)
    bad["streams"]["fixture"]["ledger_path"] = "/tmp/../secret"
    with pytest.raises(AuthorityError):
        policy.validate_manifest(bad)


def test_verification_only_factory_binds_actual_authorized_caller(stream, monkeypatch):
    import os
    _, manifest, _, _ = stream
    monkeypatch.setattr(client, "load_manifest", lambda: manifest)
    monkeypatch.setattr(client, "account_uid", lambda name: os.geteuid())
    bound = client.create_authority(Path(manifest["streams"]["fixture"]["ledger_path"]), None)
    with pytest.raises(AuthorityError, match="verification-only"):
        bound.append("op", {}, {})
    monkeypatch.setattr(client, "account_uid", lambda name: os.geteuid() + 1)
    with pytest.raises(AuthorityError, match="verification"):
        client.create_authority(Path(manifest["streams"]["fixture"]["ledger_path"]), None)


def test_provisioned_learning_inventory_can_record_principal_feedback(tmp_path, monkeypatch):
    from exo.learn.runtime import Runtime
    from exo.learn.signals import ensure_utterance
    from exo.store import PrincipalStore
    from exo_os.ledgerd.admin import inventory
    store = tmp_path / "store"
    PrincipalStore.create(store)
    keys = {writer: crypto.keygen() for writer in inventory("exo")}
    checkpoint = crypto.keygen()
    writers = {writer: {**config, "pub": crypto.pubkey_b64(keys[writer])}
               for writer, config in inventory("exo").items()}
    identity = {"version": 1, "stream_id": "learn", "checkpoint_pub": crypto.pubkey_b64(checkpoint), "writers": writers}
    journal = AuthorityJournal.provision(tmp_path / "authority.sqlite", identity, keys, checkpoint)
    manifest = {"version": 1, "streams": {"learn": {"ledger_path": str(store / "ledger.sqlite"), "identity": identity,
                "callers": {writer: ["exo"] for writer in writers}}}}
    monkeypatch.setattr(client, "load_manifest", lambda: manifest)
    def transport(self, op, **args):
        if op == "head":
            return journal.head(args["seq"])
        if op == "read_since":
            return journal.read_since(args["seq"], args["limit"])
        return journal.append(self.writer, args["operation_id"], args["expected_head"], args["intent"])
    monkeypatch.setattr(client.AuthorityClient, "_call", transport)
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "exo-os")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    rt = Runtime(store, tmp_path / "home")
    try:
        eid = ensure_utterance(rt, "session", "user", "principal feedback")
        event = rt.ledger.get(eid)
        assert event["author"] == "principal"
        assert journal.head()["writer"] == "exo-learn"
        assert not (tmp_path / "home/keys/exo-learn.key").exists()
    finally:
        rt.close()


def test_strict_wire_rejects_paths_extra_fields_and_unbounded_batches():
    raw = {"version": 1, "nonce": "n", "stream": "s", "writer": "w", "op": "read_since", "seq": 0, "limit": 64}
    assert wire.request(raw) == raw
    for change in ({"limit": 65}, {"seq": True}, {"path": "/secret"}, {"key_path": "/private"}, {"version": True}):
        with pytest.raises(AuthorityError):
            wire.request({**raw, **change})
    for data in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        with pytest.raises(ValueError):
            wire.decode(data)


def test_client_unknown_commit_retains_operation_id(stream, monkeypatch):
    journal, _, _, _ = stream
    bound = client.AuthorityClient("fixture", "exo-os", journal.identity)
    monkeypatch.setattr(bound, "_call", lambda *args, **kw: {"bad": "reply after commit"})
    with pytest.raises(AppendOutcomeUnknown) as exc:
        bound.append("same-operation", {"seq": 0, "chain": crypto.GENESIS}, intent())
    assert exc.value.operation_id == "same-operation"


def test_head_conflict_is_signed_and_not_a_new_operation(stream):
    journal, _, _, _ = stream
    journal.append("exo-os", "one", {"seq": 0, "chain": crypto.GENESIS}, intent())
    with pytest.raises(HeadConflict) as exc:
        journal.append("exo-os", "two", {"seq": 0, "chain": crypto.GENESIS}, intent())
    assert exc.value.head == journal.head() and journal.lookup("two", writer="exo-os") is None


def test_checkpoint_export_includes_genesis_and_never_reads_event_bodies(stream, monkeypatch):
    from exo_os.ledgerd.admin import export_checkpoints
    journal, _, _, _ = stream
    monkeypatch.setattr(journal, "read_since", lambda *a: pytest.fail("checkpoint export read private bodies"))
    empty = list(export_checkpoints(journal))
    assert len(empty) == 1 and empty[0]["seq"] == 0
    journal.append("exo-os", "one", {"seq": 0, "chain": crypto.GENESIS}, intent())
    assert [cp["seq"] for cp in export_checkpoints(journal)] == [0, 1]
    assert [cp["seq"] for cp in export_checkpoints(journal, 1, 1)] == [1]
    assert list(export_checkpoints(journal, 2)) == []


def test_unknown_after_authority_commit_is_lookup_recoverable(stream, monkeypatch):
    from exo_os.ledgerd.server import Server
    from exo_os.ledgerd import server
    journal, manifest, _, _ = stream
    def after_commit(point):
        if point == "after_commit":
            raise OSError("lost response after durable commit")
    journal.fault = after_commit
    raw = {"version": 1, "nonce": "n", "stream": "fixture", "writer": "exo-os", "op": "append",
           "operation_id": "same-id", "expected_head": {"seq": 0, "chain": crypto.GENESIS}, "intent": intent()}
    sent = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, *args):
            pass
        def sendall(self, value):
            sent.append(value)
    monkeypatch.setattr(server, "peer_credentials", lambda *a: (1, 555, 555))
    monkeypatch.setattr(server, "read_line", lambda *a: wire.encode(raw))
    Server(manifest, {"fixture": journal}, {"exo": 555}).serve_connection(Connection())
    assert wire.decode(sent[-1])["state"] == "unknown"
    assert journal.lookup("same-id", writer="exo-os")["event"]["seq"] == 1


def test_server_refuses_kernel_caller_before_payload_read(stream, monkeypatch):
    from exo_os.ledgerd.server import Server
    from exo_os.ledgerd import server
    journal, manifest, _, _ = stream
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def sendall(self, value):
            pytest.fail("greeting disclosed before caller authentication")
    monkeypatch.setattr(server, "peer_credentials", lambda *a: (1, 999, 999))
    monkeypatch.setattr(server, "read_line", lambda *a: pytest.fail("unauthenticated payload read"))
    Server(manifest, {"fixture": journal}, {"exo": 555}).serve_connection(Connection())


def test_os_sink_selects_factory_before_keygen(tmp_path, monkeypatch):
    from exo_os.ledger import ExoLedgerSink, open_sink
    seen = []
    class Ledger:
        def append(self, *args, **kw):
            return {"id": "saved"}
    monkeypatch.setattr("exo.ledger_authority.factory.open_writable_ledger", lambda *a, **kw: seen.append((a, kw)) or Ledger())
    monkeypatch.setattr(crypto, "keygen", lambda: pytest.fail("desktop key generated"))
    sink = ExoLedgerSink(tmp_path, tmp_path / "missing.key")
    assert sink.append("test", {}) == "saved" and seen[0][1]["writer"] == "exo-os"
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    with pytest.raises(AuthorityError):
        open_sink(None, tmp_path)
    monkeypatch.delenv("EXO_LEDGER_AUTHORITY_REQUIRED")
    assert open_sink(None, tmp_path).append("test", {}) is None


def test_os_sink_factory_projects_protected_records_without_desktop_private_key(stream, tmp_path, monkeypatch):
    from exo_os.ledger import ExoLedgerSink
    journal, manifest, _, _ = stream
    store = tmp_path / "store"
    store.mkdir()
    manifest["streams"]["fixture"]["ledger_path"] = str(store / "ledger.sqlite")
    monkeypatch.setattr(client, "load_manifest", lambda: manifest)
    def transport(self, op, **args):
        if op == "head":
            return journal.head(args["seq"])
        if op == "read_since":
            return journal.read_since(args["seq"], args["limit"])
        if op == "lookup":
            return journal.lookup(args["operation_id"], writer=self.writer)
        return journal.append(self.writer, args["operation_id"], args["expected_head"], args["intent"])
    monkeypatch.setattr(client.AuthorityClient, "_call", transport)
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY", "exo-os")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.setattr(crypto, "keygen", lambda: pytest.fail("desktop generated signer key"))
    monkeypatch.setattr(crypto, "load_key", lambda *a: pytest.fail("desktop read signer key"))
    missing_key = tmp_path / "must-not-exist.key"
    sink = ExoLedgerSink(store, missing_key)
    event_id = sink.append("fixture", {"test": True})
    assert journal.head()["seq"] == 1 and sink.ledger.get(event_id)["body"]["test"] is True
    assert not missing_key.exists()


def test_first_login_refuses_unprovisioned_stream_before_initializer(tmp_path, monkeypatch):
    from exo_os import setup
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_PRINCIPAL", "exo")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: pytest.fail("initializer before provisioning"))
    def absent(*args):
        raise AuthorityError("not provisioned")
    monkeypatch.setattr(client, "create_authority", absent)
    with pytest.raises(AuthorityError):
        setup.main()
    assert not (tmp_path / "home/principals/exo").exists()


def test_first_login_preflight_precedes_fresh_initializer_and_machine_profile(tmp_path, monkeypatch):
    from exo_os import setup
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_PRINCIPAL", "exo")
    monkeypatch.setenv("EXO_LEDGER_AUTHORITY_REQUIRED", "1")
    seen = []
    class Authority:
        def head(self):
            seen.append("preflight")
            return {"seq": 0}
    monkeypatch.setattr(client, "create_authority", lambda *a: Authority())
    def initialize(*args, **kwargs):
        assert seen == ["preflight"]
        seen.append("init")
        (tmp_path / "home/principals/exo").mkdir(parents=True)
    monkeypatch.setattr(setup.subprocess, "run", initialize)
    monkeypatch.setattr(setup.paths, "owns_desktop", lambda *a: False)
    assert setup.main() == 0 and seen == ["preflight", "init"]


def test_recovery_requires_persistent_signer_mount():
    image = Path(__file__).resolve().parents[1] / "image/mkosi.extra"
    assert "subvol=@signer" in (image / "etc/fstab").read_text()
    firstboot = (image / "usr/lib/systemd/system/exo-os-firstboot.service").read_text()
    assert "/var/lib/exo-ledger" not in firstboot
    service = (image / "usr/lib/systemd/system/exo-ledger.service").read_text()
    assert "StateDirectory=" not in service  # never chown the root-pinned manifest to the service
    assert "RestrictAddressFamilies=AF_UNIX" in service and "IPAddressDeny=any" in service


def test_boot_requires_exact_distinct_persistent_subvolumes(monkeypatch):
    from exo_os.ledgerd import admin
    store, signer = Path("/var/lib/exo"), Path("/var/lib/exo-ledger")
    monkeypatch.setattr(admin, "trusted_path", lambda path, **kwargs: path)
    uuid = "f12ac770-841b-44ce-8a7f-f00288595fbc"
    mounts = {store: {"target": str(store), "fstype": "btrfs", "fsroot": "/@store", "uuid": uuid},
              signer: {"target": str(signer), "fstype": "btrfs", "fsroot": "/@signer", "uuid": uuid}}
    monkeypatch.setattr(admin, "_mount_identity", lambda path: mounts[path])
    admin.validate_boot_mounts(signer, store)
    for changed in ({"fsroot": "/@"}, {"fstype": "ext4"}, {"uuid": "e12ac770-841b-44ce-8a7f-f00288595fbc"}):
        mounts[signer] = {**mounts[signer], **changed}
        with pytest.raises(AuthorityError):
            admin.validate_boot_mounts(signer, store)
        mounts[signer] = {"target": str(signer), "fstype": "btrfs", "fsroot": "/@signer", "uuid": uuid}


@pytest.mark.skipif(__import__("os").geteuid() != 0, reason="root-owned skeleton requires disposable root runner")
def test_boot_provisions_only_empty_root_owned_skeleton(tmp_path, monkeypatch):
    from exo_os.ledgerd import admin
    state, store = tmp_path / "signer", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    (store / "exo").mkdir()
    monkeypatch.setattr(admin, "validate_boot_mounts", lambda *a: None)
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    monkeypatch.setattr(admin, "account_uid", lambda name: 777)
    monkeypatch.setattr(__import__("pwd"), "getpwnam",
                        lambda name: type("Account", (), {"pw_gid": 777})())
    seen = []
    monkeypatch.setattr(admin, "provision", lambda *a, **kw: seen.append((a, kw)) or {"fresh": True})
    assert admin.ensure_boot(state=state, store=store) == {"fresh": True}
    assert seen[0][1]["projection"] == store / "exo/principals/exo/ledger.sqlite"
    (store / ".owned").touch()
    with pytest.raises(AuthorityError):
        admin.ensure_boot(state=state, store=store)
    (store / ".owned").unlink()
    (state / "private").mkdir()
    with pytest.raises(AuthorityError):
        admin.ensure_boot(state=state, store=store)


def test_boot_existing_manifest_validated_as_signer_without_reseed(tmp_path, monkeypatch):
    from exo_os.ledgerd import admin, server
    state, store = tmp_path / "signer", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    projection = store / "exo/principals/exo/ledger.sqlite"
    manifest = {"streams": {"fixed": {"ledger_path": str(projection)}}}
    (state / "manifest.json").write_text("pinned")
    monkeypatch.setattr(admin, "validate_boot_mounts", lambda *a: None)
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    monkeypatch.setattr(admin, "account_uid", lambda name: 777)
    monkeypatch.setattr(__import__("pwd"), "getpwnam", lambda name: type("Account", (), {"pw_gid": 778})())
    monkeypatch.setattr(admin, "load_manifest", lambda path: manifest)
    calls = []
    monkeypatch.setattr(server, "load_journals", lambda *a: calls.append(a))
    monkeypatch.setattr(admin, "_as_signer", lambda uid, gid, action: calls.append((uid, gid)) or action())
    monkeypatch.setattr(admin, "provision", lambda *a, **kw: pytest.fail("existing state reseeded"))
    assert admin.ensure_boot(state=state, store=store) == manifest
    assert calls == [(777, 778), (manifest, 777, state)]
    manifest["streams"]["fixed"]["ledger_path"] = str(store / "elsewhere")
    with pytest.raises(AuthorityError):
        admin.ensure_boot(state=state, store=store)


def test_boot_rejects_root_home_without_normalizing_fresh_store(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace
    from exo_os.ledgerd import admin

    state, store = tmp_path / "signer", tmp_path / "store"
    state.mkdir()
    store.mkdir()
    (store / "exo").mkdir(mode=0o700)
    (store / "root").mkdir(mode=0o700)
    monkeypatch.setattr(admin, "validate_boot_mounts", lambda *a: None)
    monkeypatch.setattr(admin.os, "geteuid", lambda: 0)
    monkeypatch.setattr(admin, "account_uid", lambda name: 777)
    monkeypatch.setattr(__import__("pwd"), "getpwnam", lambda name: SimpleNamespace(pw_gid=778))
    # The image principal skeleton is root-owned; host tests use a modeled UID.
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0))
    monkeypatch.setattr(admin, "provision", lambda *a, **kw: pytest.fail("unexpected store provisioned"))
    with pytest.raises(AuthorityError, match="unexpected entries"):
        admin.ensure_boot(state=state, store=store)
    assert (store / "root").is_dir()


def test_image_login_and_socket_require_boot_provisioning():
    image = Path(__file__).resolve().parents[1] / "image/mkosi.extra/usr/lib"
    units = image / "systemd/system"
    assert "ExecStart=/opt/exo/bin/exo-ledger-admin ensure-boot --principal exo" in (
        units / "exo-ledger-provision.service").read_text()
    for path in (units / "exo-os-firstboot.service", units / "exo-ledger.socket", units / "exo-ledger.service",
                 units / "greetd.service.d/10-exo-ledger.conf",
                 units / "systemd-user-sessions.service.d/10-exo-ledger.conf"):
        contents = path.read_text()
        assert "Requires=exo-ledger-provision.service" in contents
        assert "After=exo-ledger-provision.service" in contents
    env = (image / "environment.d/50-exo-os.conf").read_text()
    assert "EXO_LEDGER_AUTHORITY=exo-os" in env and "EXO_LEDGER_AUTHORITY_REQUIRED=1" in env
    for name in ("exod.service", "exo-os-setup.service"):
        unit = (image / "systemd/user" / name).read_text()
        assert "ConditionUser=exo" in unit.split("[Service]", 1)[0]
        assert "Environment=EXO_LEDGER_AUTHORITY=exo-os" in unit
        assert "Environment=EXO_LEDGER_AUTHORITY_REQUIRED=1" in unit
    helper = (Path(__file__).resolve().parents[1] /
              "image/mkosi.profiles/vmtest/mkosi.extra/usr/local/bin/as-exo").read_text()
    assert "EXO_LEDGER_AUTHORITY=exo-os" in helper and "EXO_LEDGER_AUTHORITY_REQUIRED=1" in helper


@pytest.mark.parametrize("alias", [False, True])
def test_writer_keys_compare_decoded_material(stream, alias):
    _, manifest, _, _ = stream
    raw = copy.deepcopy(manifest)
    entry = raw["streams"]["fixture"]
    pub = entry["identity"]["writers"]["exo-os"]["pub"]
    if alias:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        pub = pub[:-2] + alphabet[alphabet.index(pub[-2]) | 1] + "="
        assert crypto.unb64(pub) == crypto.unb64(entry["identity"]["writers"]["exo-os"]["pub"])
    entry["identity"]["writers"]["other"] = {"pub": pub, "author": "other", "authors": ["other"]}
    entry["callers"]["other"] = ["exo"]
    with pytest.raises(AuthorityError, match="independently provisioned"):
        policy.validate_manifest(raw)


def test_distinct_writer_material_preserves_pinned_manifest(stream):
    _, manifest, _, _ = stream
    raw = copy.deepcopy(manifest)
    entry = raw["streams"]["fixture"]
    entry["identity"]["writers"]["other"] = {"pub": crypto.pubkey_b64(crypto.keygen()),
                                                "author": "other", "authors": ["other"]}
    entry["callers"]["other"] = ["exo"]
    before = copy.deepcopy(raw)
    assert policy.validate_manifest(raw) == before
    assert raw == before

"""Host-side protected state and real loopback mTLS, with UID enforcement tested separately on Linux."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import os
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from exo import crypto
from exo.ledger_authority.journal import AuthorityJournal
from exo.ledger_authority.witness import WitnessJournal
from exo.ledger_authority.witness_transport import WitnessClient, WitnessServer

from exo_os.witness import admin, process, state, worker


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    path = tmp_path_factory.mktemp("publisher-certs")
    def openssl(*args):
        subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True, timeout=10)
    ca, ca_key = path / "ca.pem", path / "ca.key"
    openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", ca_key)
    openssl("req", "-sha256", "-new", "-x509", "-key", ca_key, "-out", ca, "-days", "1", "-subj", "/CN=test-CA")
    result = {"ca": ca}
    for name, purpose in (("server", "serverAuth"), ("client", "clientAuth")):
        key, cert, csr, ext = (path / (name + suffix) for suffix in (".key", ".pem", ".csr", ".ext"))
        openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key)
        openssl("req", "-new", "-key", key, "-out", csr, "-subj", "/CN=" + name)
        ext.write_text(f"basicConstraints=CA:FALSE\nextendedKeyUsage={purpose}\nsubjectAltName=DNS:witness.test\n")
        openssl("x509", "-sha256", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key, "-CAcreateserial",
                "-out", cert, "-days", "1", "-extfile", ext)
        result[name] = {"key": key, "cert": cert, "pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()}
    return result


@pytest.fixture
def setup(tmp_path, monkeypatch, certificates):
    # Simulate only root ownership on a host user-owned test tree; all descriptor/type/mode checks remain real.
    owner = os.getuid()
    check = state.check_node
    def host_node(info, **kw):
        info = SimpleNamespace(st_uid=0 if info.st_uid == owner else info.st_uid, st_mode=info.st_mode,
                               st_nlink=info.st_nlink)
        return check(info, **kw)
    monkeypatch.setattr(state, "check_node", host_node)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    @contextlib.contextmanager
    def host_snapshots(credentials, spool, gid, **kw):
        with tempfile.TemporaryDirectory(dir=tmp_path) as path:
            for name, raw in {**credentials, "checkpoints.jsonl": spool}.items():
                (Path(path) / name).write_bytes(raw)
            yield path
    monkeypatch.setattr(admin, "snapshots", host_snapshots)
    writer, checkpoint_key, witness_key = crypto.keygen(), crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "publisher-test", "checkpoint_pub": crypto.pubkey_b64(checkpoint_key),
                "writers": {"agent": {"pub": crypto.pubkey_b64(writer), "author": "agent", "authors": ["agent"]}}}
    authority = AuthorityJournal.provision(tmp_path / "authority.sqlite", identity, {"agent": writer}, checkpoint_key)
    genesis = authority.head()
    witness = WitnessJournal.provision(tmp_path / "witness.sqlite", identity, witness_key, genesis)
    certs = certificates
    server = WitnessServer(witness, genesis, ca=certs["ca"], cert=certs["server"]["cert"],
                           tls_key=certs["server"]["key"], client_cert_sha256=certs["client"]["pin"], timeout=0.5)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    policy = {"version": 1, "identity": identity, "genesis": genesis, "witness_pub": crypto.pubkey_b64(witness_key),
              "host": "127.0.0.1", "port": server.address[1], "server_hostname": "witness.test",
              "server_cert_sha256": certs["server"]["pin"], "timeout": 1}
    def bound(got):
        if got["identity"] != identity:
            raise state.WitnessError("wrong protected authority")
    real_bound = admin.bound_authority
    monkeypatch.setattr(admin, "bound_authority", bound)
    bundle, store = tmp_path / "bundle", tmp_path / "store"
    bundle.mkdir(mode=0o700)
    store.mkdir(mode=0o755)
    (store / "exo").mkdir(mode=0o700)
    credentials = {"ca.pem": certs["ca"].read_bytes(), "client.pem": certs["client"]["cert"].read_bytes(),
                   "client.key": certs["client"]["key"].read_bytes()}
    for name, raw in {"policy.json": state.encoded(policy), "receipt.json": state.encoded(witness.head()), **credentials}.items():
        (bundle / name).write_bytes(raw)
        (bundle / name).chmod(0o600)
    class Bridge:
        calls = 0
        reply = None
        after_export = None
        bad_export = None
        def __init__(self):
            self.nonces = []
            self.requests = []
        def export(self, config):
            out = io.BytesIO()
            worker.export_prefix(authority, out)
            if self.after_export:
                self.after_export()
            return self.bad_export if self.bad_export is not None else out.getvalue()
        def network(self, config, creds, spool, *, publish, nonce):
            self.calls += 1
            self.nonces.append(nonce)
            self.requests.append((publish, spool))
            with host_snapshots(creds, spool, 0) as path:
                client = WitnessClient(config["identity"], config["genesis"], config["witness_pub"],
                    **{k: config[k] for k in ("host", "port", "server_hostname", "server_cert_sha256", "timeout")},
                    ca=Path(path) / "ca.pem", cert=Path(path) / "client.pem", tls_key=Path(path) / "client.key")
                receipt = client.publish(Path(path) / "checkpoints.jsonl") if publish else None
                response = {"receipt": receipt, "head": client.query_head(nonce)}
                return state.encoded(self.reply(response) if self.reply else response)
    item = SimpleNamespace(base=store / "witness-publisher", bundle=bundle, policy=policy, authority=authority,
                           witness=witness, bridge=Bridge(), key=witness_key, writer=writer,
                           checkpoint_key=checkpoint_key, real_bound=real_bound)
    try:
        yield item
    finally:
        server.close()
        thread.join(2)
        assert not thread.is_alive()


def append(journal, n="one"):
    head = journal.head()
    return journal.append("agent", n, {"seq": head["seq"], "chain": head["chain"]},
        {"author": "agent", "kind": "note", "body": {"text": "PRIVATE_BODY"}, "session": None, "refs": []})["checkpoint"]


def enroll(s):
    assert admin.initialize(s.bundle, base=s.base)["state"] == "initialized"


def run(s, **kw):
    return admin.operate(base=s.base, workers=s.bridge, **kw)


def alias_public_key(pub):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    assert len(pub) == 44 and pub.endswith("=")
    last = alphabet.index(pub[-2])
    assert last & 3 == 0
    alias = pub[:-2] + alphabet[last | 1] + "="
    assert alias != pub and crypto.unb64(alias) == crypto.unb64(pub)
    return alias


@pytest.mark.parametrize("source", ["checkpoint", "writer"])
@pytest.mark.parametrize("alias", [False, True])
def test_policy_requires_distinct_witness_key_bytes(setup, source, alias):
    s = setup
    authority_pub = (s.policy["identity"]["checkpoint_pub"] if source == "checkpoint"
                     else s.policy["identity"]["writers"]["agent"]["pub"])
    candidate = alias_public_key(authority_pub) if alias else authority_pub
    with pytest.raises(state.WitnessError, match="separate witness key"):
        state.validate_policy({**s.policy, "witness_pub": candidate})
    assert state.validate_policy(s.policy) == s.policy


def test_disabled_and_explicit_offline_initialization(setup):
    s = setup
    assert run(s) == {"state": "disabled", "fresh": False}
    assert not s.base.exists()
    enroll(s)
    assert s.bridge.calls == 0
    assert run(s)["state"] == "cached_receipt"
    with pytest.raises(state.WitnessError, match="already exists"):
        enroll(s)


def test_publish_fresh_and_cached_status_are_distinct_and_status_does_not_write(setup):
    s = setup
    enroll(s)
    append(s.authority)
    assert run(s, fresh=True)["state"] == "fresh_witness_behind"
    assert run(s, publish=True)["state"] == "published"
    before = (s.base / "current.json").read_bytes()
    assert run(s)["evidence"]["receipt"]["seq"] == 1
    assert run(s, fresh=True)["state"] == "fresh_match_at_snapshot"
    assert (s.base / "current.json").read_bytes() == before
    assert len(set(s.bridge.nonces)) == 4
    assert [publish for publish, _ in s.bridge.requests] == [False, False, True, False]
    assert all(not spool for publish, spool in s.bridge.requests if not publish)
    assert b"PRIVATE_BODY" not in before


@pytest.mark.parametrize("missing", ["current.json", "previous.json", "policy.json", "client.key", "marker", "directory"])
def test_enabled_missing_state_never_reseeds(setup, missing):
    s = setup
    enroll(s)
    if missing == "marker":
        (s.base.parent / state.MARKER).unlink()
    elif missing == "directory":
        s.base.rename(s.base.with_name("saved"))
    else:
        (s.base / missing).unlink()
    with pytest.raises((state.WitnessError, OSError)):
        run(s, publish=True)
    assert s.bridge.calls == 0


@pytest.mark.parametrize("damage", ["mode", "symlink", "hardlink", "fifo", "huge", "duplicate", "corrupt", "marker_hash", "ancestor_symlink"])
def test_protected_state_rejects_bad_types_modes_and_json(setup, damage):
    s = setup
    enroll(s)
    path = s.base / "policy.json"
    if damage == "mode":
        path.chmod(0o622)
    elif damage == "symlink":
        path.rename(s.base / "other")
        path.symlink_to(s.base / "other")
    elif damage == "hardlink":
        os.link(path, s.base / "other")
    elif damage == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    elif damage == "huge":
        path.write_bytes(b" " * (state.MAX_FILE + 1))
    elif damage == "duplicate":
        path.write_text('{"version":1,"version":1}')
    elif damage == "corrupt":
        (s.base / "current.json").write_text("null")
    elif damage == "marker_hash":
        (s.base.parent / state.MARKER).write_text('{"version":1,"policy_hash":"wrong"}')
    else:
        real = s.base.with_name("real")
        s.base.rename(real)
        s.base.symlink_to(real)
    with pytest.raises((state.WitnessError, OSError)):
        run(s)
    assert s.bridge.calls == 0


def test_partial_initialization_leaves_no_enabled_marker_and_cannot_retry(setup, monkeypatch):
    s = setup
    original = admin.write_new
    def fail(fd, name, raw):
        if name == "previous.json":
            raise OSError("disk failure")
        original(fd, name, raw)
    monkeypatch.setattr(admin, "write_new", fail)
    with pytest.raises(OSError):
        enroll(s)
    assert s.base.exists() and not (s.base.parent / state.MARKER).exists()
    with pytest.raises(state.WitnessError, match="partial"):
        run(s)
    with pytest.raises(state.WitnessError, match="already exists"):
        enroll(s)


def test_replayed_child_envelope_fails_parent_nonce_verification(setup):
    s = setup
    enroll(s)
    captured = []
    s.bridge.reply = lambda reply: captured.append(copy.deepcopy(reply)) or reply
    run(s, fresh=True)
    s.bridge.reply = lambda reply: captured[0]
    with pytest.raises(state.WitnessError, match="replayed"):
        run(s, fresh=True)


def test_ahead_of_captured_target_is_not_silently_chased(setup):
    s = setup
    enroll(s)
    def advance():
        s.witness.observe(append(s.authority))
    s.bridge.after_export = advance
    assert run(s, fresh=True)["state"] == "fresh_witness_ahead"
    assert run(s)["evidence"]["receipt"]["seq"] == 0


def test_valid_witness_fork_is_conflict_not_lag(setup, tmp_path):
    s = setup
    enroll(s)
    append(s.authority)
    fork = AuthorityJournal.provision(tmp_path / "fork.sqlite", s.policy["identity"], {"agent": s.writer}, s.checkpoint_key)
    head = fork.head()
    cp = fork.append("agent", "fork", {"seq": head["seq"], "chain": head["chain"]},
        {"author": "agent", "kind": "note", "body": {"text": "different"}, "session": None, "refs": []})["checkpoint"]
    s.witness.observe(cp)
    with pytest.raises(state.WitnessError, match="conflicts"):
        run(s, fresh=True)


def test_regression_behind_durable_receipt_is_conflict(setup):
    import sqlite3
    s = setup
    enroll(s)
    append(s.authority)
    run(s, publish=True)
    with sqlite3.connect(s.witness.path) as db:
        db.execute("DELETE FROM witnessed WHERE seq>0")
    with pytest.raises(state.WitnessError, match="regressed"):
        run(s, fresh=True)


def test_reply_loss_then_identical_retry_preserves_evidence(setup):
    s = setup
    enroll(s)
    append(s.authority)
    before = (s.base / "current.json").read_bytes()
    def fault(point):
        if point == "after_commit":
            raise OSError("lost reply")
    s.witness.fault = fault
    with pytest.raises(ValueError):
        run(s, publish=True)
    assert (s.base / "current.json").read_bytes() == before
    assert s.witness.head()["seq"] == 1
    assert run(s, publish=True)["state"] == "published"


@pytest.mark.parametrize("failure", ["partial", "timeout"])
def test_post_commit_child_reply_failure_preserves_both_evidence_files(setup, failure):
    s = setup
    enroll(s)
    append(s.authority)
    before = {name: (s.base / name).read_bytes() for name in ("current.json", "previous.json")}
    original = s.bridge.network
    def fail(*args, **kwargs):
        reply = original(*args, **kwargs)
        if not kwargs["publish"]:
            return reply
        if failure == "timeout":
            raise state.WitnessError("witness helper deadline exceeded", "unavailable")
        return b'{"receipt":'
    s.bridge.network = fail
    with pytest.raises(state.WitnessError) as error:
        run(s, publish=True)
    assert error.value.state == "unavailable"
    assert s.witness.head()["seq"] == 1
    assert before == {name: (s.base / name).read_bytes() for name in before}


@pytest.mark.parametrize("failure", [1, 2, 3, 4])
def test_every_persistence_fsync_failure_preserves_prior_verified_evidence(setup, monkeypatch, failure):
    s = setup
    enroll(s)
    cp = append(s.authority)
    receipt = s.witness.observe(cp)
    with state.established(s.base) as (fd, policy, current, _):
        updated = state.evidence(policy, cp, receipt, cp, admin.now())
        count = 0
        fsync = os.fsync
        def fail(descriptor):
            nonlocal count
            count += 1
            if count == failure:
                raise OSError("fsync failed")
            fsync(descriptor)
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fail)
            with pytest.raises(OSError):
                state.persist(fd, policy, current, updated)
        saved = [state.validate_evidence(policy, state.decode(state.read_file(fd, name))) for name in ("current.json", "previous.json")]
        assert current in saved


def test_multiple_pages_keep_first_target_while_head_grows(setup):
    s = setup
    for n in range(70):
        append(s.authority, str(n))
    original = s.authority.head
    class Growing:
        def head(self, seq=None):
            got = original(seq)
            if seq is not None:
                append(s.authority, "growth-" + str(seq))
            return got
    output = io.BytesIO()
    worker.export_prefix(Growing(), output)
    exported = admin.Export(s.policy, output.getvalue())
    assert exported.target["seq"] == 70 and original()["seq"] == 141
    assert len(exported.data.splitlines()) == 71


def test_partial_or_invalid_export_never_starts_network(setup):
    s = setup
    enroll(s)
    append(s.authority)
    output = io.BytesIO()
    worker.export_prefix(s.authority, output)
    s.bridge.bad_export = b"\n".join(output.getvalue().splitlines()[:-1]) + b"\n"
    with pytest.raises(state.WitnessError, match="exactly"):
        run(s, publish=True)
    assert s.bridge.calls == 0


def test_subprocess_output_and_deadline_are_bounded():
    for code, limit, timeout in (("print('x'*10000)", 8, 2),
                                 ("import sys; sys.stderr.write('x'*10000)", 8192, 2),
                                 ("import time; time.sleep(30)", 8192, 0.1)):
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        with pytest.raises(state.WitnessError):
            process.collect(proc, limit, timeout)
        assert proc.poll() is not None


def test_exec_launch_clears_groups_env_and_drops_before_worker(monkeypatch):
    calls = []
    monkeypatch.setattr("exo_os.sandbox.policy.trusted_path", lambda p: p)
    monkeypatch.setattr(Path, "resolve", lambda p, **kw: p)
    monkeypatch.setattr(process.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)) or object())
    monkeypatch.setattr(process, "collect", lambda proc, limit: b"ok")
    assert process.execute("export", {"policy": {}}, (501, 502), limit=8) == b"ok"
    argv, kw = calls[0]
    assert argv[:5] == [process.PYTHON, "-I", "-m", "exo_os.witness.worker", "export"]
    assert (kw["user"], kw["group"], kw["extra_groups"]) == (501, 502, [])
    assert kw["start_new_session"] and kw["close_fds"] and "shell" not in kw
    assert "PYTHONPATH" not in kw["env"] and "HTTPS_PROXY" not in kw["env"]


def test_disabled_import_does_not_require_core_witness_dependencies():
    code = '''
import contextlib, importlib.abc, os, sys
class NoCore(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        assert not (fullname == "exo" or fullname.startswith("exo.")), fullname
sys.meta_path.insert(0, NoCore())
from exo_os.witness import admin
@contextlib.contextmanager
def disabled(base):
    yield None
admin.established = disabled
os.geteuid = lambda: 0
assert admin.operate() == {"state":"disabled", "fresh":False}
'''
    proc = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0, proc.stderr


def test_non_root_file_owner_is_refused():
    import stat
    with pytest.raises(state.WitnessError, match="ownership"):
        state.check_node(SimpleNamespace(st_uid=991, st_mode=stat.S_IFREG | 0o600, st_nlink=1))


def test_read_uses_checked_descriptor_if_path_is_replaced(setup, monkeypatch):
    s = setup
    enroll(s)
    original = os.fstat
    swapped = False
    with state.directory(s.base) as fd:
        def swap(child):
            nonlocal swapped
            info = original(child)
            if not swapped:
                swapped = True
                path = s.base / "policy.json"
                path.rename(s.base / "original-policy")
                path.write_bytes(b"hostile replacement")
            return info
        monkeypatch.setattr(os, "fstat", swap)
        assert state.decode(state.read_file(fd, "policy.json")) == s.policy


@pytest.mark.parametrize("failure", ["not_provisioned", "credentials", "receipt", "marker"])
def test_initialization_failure_never_enables_or_contacts_witness(setup, monkeypatch, failure):
    s = setup
    if failure == "not_provisioned":
        def missing(policy):
            raise FileNotFoundError("manifest absent")
        monkeypatch.setattr(admin, "bound_authority", missing)
    elif failure == "credentials":
        (s.bundle / "client.key").write_text("invalid TLS private key")
    elif failure == "receipt":
        (s.bundle / "receipt.json").write_bytes(state.encoded({"forged": True}))
    else:
        original = admin.write_new
        def fail(fd, name, raw):
            if name == state.MARKER:
                raise OSError("marker write failed")
            original(fd, name, raw)
        monkeypatch.setattr(admin, "write_new", fail)
    with pytest.raises((ValueError, OSError)):
        enroll(s)
    assert not (s.base.parent / state.MARKER).exists() and s.bridge.calls == 0
    assert s.base.exists() == (failure == "marker")


def test_policy_must_match_protected_authority_manifest(setup, tmp_path, monkeypatch):
    from exo_os.ledgerd import policy
    s = setup
    manifest = tmp_path / "manifest.json"
    value = {"version": 1, "streams": {"publisher-test": {"identity": copy.deepcopy(s.policy["identity"]),
             "ledger_path": "/var/lib/exo/exo/test.jsonl", "callers": {"agent": ["exo"]}}}}
    manifest.write_bytes(state.encoded(value))
    manifest.chmod(0o644)
    monkeypatch.setattr(policy, "MANIFEST", manifest)
    s.real_bound(s.policy)
    value["streams"]["publisher-test"]["identity"]["checkpoint_pub"] = crypto.pubkey_b64(crypto.keygen())
    manifest.write_bytes(state.encoded(value))
    with pytest.raises(state.WitnessError, match="provisioned authority"):
        s.real_bound(s.policy)


@pytest.mark.parametrize("reply", ["forged_receipt", "wrong_target", "unexpected_receipt", "unavailable"])
def test_child_result_cannot_replace_prior_evidence(setup, reply):
    s = setup
    enroll(s)
    before = (s.base / "current.json").read_bytes()
    old_receipt = s.witness.head()
    append(s.authority)
    def alter(value):
        if reply == "unavailable":
            raise state.WitnessError("lost connection", "unavailable")
        if reply in ("forged_receipt", "wrong_target") and value["receipt"] is None:
            return value
        if reply == "forged_receipt":
            value["receipt"]["signature"] = "forged"
        elif reply == "wrong_target":
            value["receipt"] = old_receipt
        else:
            value["receipt"] = old_receipt
        return value
    s.bridge.reply = alter
    with pytest.raises(state.WitnessError):
        run(s, publish=reply != "unexpected_receipt", fresh=reply == "unexpected_receipt")
    assert (s.base / "current.json").read_bytes() == before


def test_publication_refuses_witness_ahead_of_immutable_export(setup):
    s = setup
    enroll(s)
    before = (s.base / "current.json").read_bytes()
    s.bridge.after_export = lambda: s.witness.observe(append(s.authority))
    with pytest.raises(ValueError, match="ahead"):
        run(s, publish=True)
    assert (s.base / "current.json").read_bytes() == before


def test_invalid_later_checkpoint_refuses_network_and_fixed_export_cap(setup, monkeypatch):
    s = setup
    enroll(s)
    append(s.authority)
    out = io.BytesIO()
    worker.export_prefix(s.authority, out)
    lines = out.getvalue().splitlines(keepends=True)
    later = state.decode(lines[-1])
    later["signature"] = "forged"
    s.bridge.bad_export = b"".join(lines[:-1]) + state.encoded(later)
    with pytest.raises(ValueError):
        run(s, publish=True)
    assert s.bridge.calls == 0
    monkeypatch.setattr(admin, "MAX_EXPORT", 8)
    with pytest.raises(state.WitnessError, match="bound|framing"):
        admin.Export(s.policy, out.getvalue())


def test_evidence_target_is_exact_receipt_checkpoint(setup):
    s = setup
    current = state.evidence(s.policy, s.policy["genesis"], s.witness.head(), append(s.authority), admin.now())
    with pytest.raises(state.WitnessError, match="exactly"):
        state.validate_evidence(s.policy, current)


@pytest.mark.parametrize("bad", ["uid", "gid", "root", "members", "group_alias"])
def test_static_publisher_inventory_refuses_shared_identity(monkeypatch, bad):
    @contextlib.contextmanager
    def directory(path):
        yield 1
    monkeypatch.setattr(process, "directory", directory)
    uid, gid = (501 if bad == "uid" else 0 if bad == "root" else 503), (502 if bad == "gid" else 504)
    passwd = f"exo-ledger:x:501:502::/:/bin/false\nexo-witness:x:{uid}:{gid}::/:/bin/false\nexo:x:1000:1000::/:/bin/false\n"
    group = f"exo-witness:x:{gid}:{'exo' if bad == 'members' else ''}\n"
    if bad == "group_alias":
        group += f"exo-sys:x:{gid}:\n"
    monkeypatch.setattr(process, "read_file", lambda fd, name, **kw: (passwd if name == "passwd" else group).encode())
    with pytest.raises(state.WitnessError):
        process.identities()


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0 or
                    os.environ.get("EXO_WITNESS_LINUX_ACCEPTANCE") != "1",
                    reason="requires explicit Linux root acceptance with installed static accounts")
def test_real_network_worker_landlock_and_exec_roundtrip(tmp_path, certificates):
    """Real separate publisher exec, immutable /run credentials, Landlock, and loopback TLS."""
    from exo.ledger_authority.witness_transport import verify_head
    writer, checkpoint_key, witness_key = crypto.keygen(), crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "kernel-test", "checkpoint_pub": crypto.pubkey_b64(checkpoint_key),
                "writers": {"agent": {"pub": crypto.pubkey_b64(writer), "author": "agent", "authors": ["agent"]}}}
    authority = AuthorityJournal.provision(tmp_path / "authority.sqlite", identity, {"agent": writer}, checkpoint_key)
    genesis = authority.head()
    witness = WitnessJournal.provision(tmp_path / "witness.sqlite", identity, witness_key, genesis)
    certs = certificates
    server = WitnessServer(witness, genesis, ca=certs["ca"], cert=certs["server"]["cert"],
                           tls_key=certs["server"]["key"], client_cert_sha256=certs["client"]["pin"], timeout=1)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    policy = {"version": 1, "identity": identity, "genesis": genesis, "witness_pub": crypto.pubkey_b64(witness_key),
              "host": "127.0.0.1", "port": server.address[1], "server_hostname": "witness.test",
              "server_cert_sha256": certs["server"]["pin"], "timeout": 2}
    credentials = {"ca.pem": certs["ca"].read_bytes(), "client.pem": certs["client"]["cert"].read_bytes(),
                   "client.key": certs["client"]["key"].read_bytes()}
    try:
        cp = append(authority)
        out = io.BytesIO()
        worker.export_prefix(authority, out)
        exported = admin.Export(policy, out.getvalue())
        nonce = "a" * 64
        reply = state.decode(process.Workers().network(policy, credentials, exported.data, publish=True, nonce=nonce))
        receipt = verify_head(identity, genesis, reply["head"], policy["witness_pub"], nonce)
        assert receipt == reply["receipt"] == witness.head() and receipt["seq"] == cp["seq"]
    finally:
        server.close()
        thread.join(2)
        assert not thread.is_alive()

def test_publish_refuses_regression_before_witness_can_repair_it(setup):
    import sqlite3
    s = setup
    enroll(s)
    append(s.authority)
    run(s, publish=True)
    before = {name: (s.base / name).read_bytes() for name in ("current.json", "previous.json")}
    with sqlite3.connect(s.witness.path) as db:
        db.execute("DELETE FROM witnessed WHERE seq>0")
    with pytest.raises(state.WitnessError, match="regressed") as error:
        run(s, publish=True)
    assert error.value.state == "conflict"
    assert s.witness.head()["seq"] == 0
    assert s.bridge.requests[-1] == (False, b"")
    assert sum(publish for publish, _ in s.bridge.requests) == 1
    assert before == {name: (s.base / name).read_bytes() for name in before}

def test_publish_conflicting_witness_fork_is_conflict_before_publication(setup, tmp_path):
    s = setup
    enroll(s)
    append(s.authority)
    fork = AuthorityJournal.provision(tmp_path / "fork.sqlite", s.policy["identity"],
                                      {"agent": s.writer}, s.checkpoint_key)
    checkpoint = append(fork, "fork")
    s.witness.observe(checkpoint)
    before = (s.base / "current.json").read_bytes()
    with pytest.raises(state.WitnessError, match="conflicts") as error:
        run(s, publish=True)
    assert error.value.state == "conflict"
    assert s.bridge.requests == [(False, b"")]
    assert s.witness.head()["chain"] == checkpoint["chain"]
    assert (s.base / "current.json").read_bytes() == before


@pytest.mark.parametrize("damage", ["signature", "nonce", "replay", "receipt", "partial"])
def test_parent_refuses_untrusted_prepublication_head_before_publish(setup, damage):
    s = setup
    enroll(s)
    append(s.authority)
    captured = []
    s.bridge.reply = lambda reply: captured.append(copy.deepcopy(reply)) or reply
    run(s, fresh=True)
    s.bridge.requests.clear()
    before = (s.base / "current.json").read_bytes()
    def corrupt(reply):
        if damage == "replay":
            return captured[0]
        if damage in ("nonce", "signature"):
            reply["head"]["nonce" if damage == "nonce" else "sig"] = "a" * 64 if damage == "nonce" else "forged"
        elif damage == "receipt":
            reply["receipt"] = s.witness.head()
        return reply
    s.bridge.reply = corrupt
    if damage == "partial":
        original = s.bridge.network
        def partial(*args, **kwargs):
            original(*args, **kwargs)
            return b'{"receipt":'
        s.bridge.network = partial
    with pytest.raises(state.WitnessError) as error:
        run(s, publish=True)
    assert error.value.state == "unavailable"
    assert s.bridge.requests == [(False, b"")]
    assert s.witness.head()["seq"] == 0
    assert (s.base / "current.json").read_bytes() == before


def test_parent_refuses_publication_when_challenges_collide(setup, monkeypatch):
    s = setup
    enroll(s)
    append(s.authority)
    monkeypatch.setattr(admin.secrets, "token_hex", lambda *args: "a" * 64)
    with pytest.raises(state.WitnessError, match="distinct"):
        run(s, publish=True)
    assert s.bridge.requests == [(False, b"")]
    assert s.witness.head()["seq"] == 0


@pytest.mark.parametrize("race", [False, True])
def test_worker_parent_death_guard_installs_sigkill_and_refuses_parent_race(monkeypatch, race):
    calls = []
    class Libc:
        def prctl(self, *arguments):
            calls.append(arguments)
            return 0
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setattr(worker, "account", lambda _: (1001, 1002))
    monkeypatch.setattr(worker.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(worker.os, "getuid", lambda: 1001)
    monkeypatch.setattr(worker.os, "getegid", lambda: 1002)
    monkeypatch.setattr(worker.os, "getgroups", lambda: [])
    monkeypatch.setattr(worker.os, "getppid", lambda: 122 if race else 123)
    monkeypatch.setattr(worker.ctypes, "CDLL", lambda *args, **kwargs: Libc())
    monkeypatch.setattr(worker.resource, "getrlimit", lambda _: (1000, 1000))
    monkeypatch.setattr(worker.resource, "setrlimit", lambda *args: None)
    if race:
        with pytest.raises(state.WitnessError, match="parent is no longer available"):
            worker.restrict("network", 123)
    else:
        worker.restrict("network", 123)
    assert calls[:2] == [(38, 1, 0, 0, 0), (1, 9, 0, 0, 0)]

import json
import os
import tempfile
from pathlib import Path
import socket
import subprocess
import threading

import pytest

from exo_os.sysd.actions import ActionError, Actions
from exo_os.sysd.client import CompletedButUnrecorded, SysdClient, SysdError
from exo_os.sysd.server import Server


KEYRING = str(__import__("pathlib").Path(__file__).parent / "data" / "keyring")


class FakeRunner:
    def __init__(self, snap_pac=True, repo=("htop", "foot")):
        self.calls, self.snap_pac, self.repo, self.n = [], snap_pac, set(repo), 10

    def __call__(self, argv):
        self.calls.append(argv)
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if argv[:2] == ["pacman", "-Q"]:
            return subprocess.CompletedProcess(argv, 0 if self.snap_pac else 1, "", "")
        if argv[:2] == ["pacman", "-Si"]:
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in self.repo else 1, "", "")
        if argv[0] == "snapper" and "create" in argv:
            self.n += 1
            return ok(f"{self.n}\n")
        if argv[0] == "snapper" and "list" in argv:
            return ok(json.dumps({"root": [{"number": 0}, {"number": 11, "description": "pre", "type": "pre"},
                                           {"number": 12, "description": "post", "type": "post", "pre-number": 11},
                                           {"number": 13, "type": "single"}]}))
        return ok()


def test_package_install_with_snap_pac():
    r = FakeRunner()
    out = Actions(r, keyring=KEYRING).package_install({"names": ["htop"]})
    assert out == {"installed": ["htop"], "pre": 11, "post": 12}
    assert r.calls.index(["pacman", "-Sy", "--noconfirm"]) < r.calls.index(["pacman", "-Su", "--needed", "--noconfirm", "--", "htop"])
    assert not any("create" in c for c in r.calls)


def test_package_install_without_snap_pac_takes_own_snapshots():
    r = FakeRunner(snap_pac=False)
    out = Actions(r, keyring=KEYRING).package_install({"names": ["htop", "foot"]})
    assert out["pre"] == 11 and out["post"] == 12
    creates = [c for c in r.calls if "create" in c]
    assert len(creates) == 2 and "pre" in creates[0] and creates[1][-2:] == ["--pre-number", "11"]


@pytest.mark.parametrize("names", [[], ["-Syu"], ["htop; rm -rf /"], ["../x"], ["a"] * 11, "htop", [3]])
def test_package_names_are_checked(names):
    with pytest.raises(ActionError):
        Actions(FakeRunner(), keyring=KEYRING).package_install({"names": names})


def test_unknown_package_refused_before_pacman_runs():
    r = FakeRunner()
    with pytest.raises(ActionError, match="not in the package repositories: nope"):
        Actions(r, keyring=KEYRING).package_install({"names": ["htop", "nope"]})
    assert not any(c[:2] == ["pacman", "-Su"] for c in r.calls)


def test_snapshot_description_checked():
    with pytest.raises(ActionError):
        Actions(FakeRunner(), keyring=KEYRING).snapshot_create({"description": "x\n--rm"})


def test_keyring_is_not_ready_until_populated(tmp_path):
    # `pacman-key --init` creates trustdb.gpg at once; only the marker written after --populate means ready.
    (tmp_path / "trustdb.gpg").write_text("")
    actions = Actions(FakeRunner(), keyring=str(tmp_path))
    assert not actions.keyring_ready()
    with pytest.raises(ActionError, match="keyring is still being set up"):
        actions.package_install({"names": ["htop"]})
    (tmp_path / "exo-os-populated").write_text("")
    assert actions.keyring_ready()


def test_install_says_so_when_keyring_setup_failed(tmp_path):
    class FailedUnit(FakeRunner):
        def __call__(self, argv):
            if argv[:2] == ["systemctl", "show"]:
                return subprocess.CompletedProcess(argv, 0, "Result=exit-code\nActiveState=failed\n", "")
            return super().__call__(argv)

    r = FailedUnit()
    with pytest.raises(ActionError, match="could not be set up at first start; restarting the machine"):
        Actions(r, keyring=str(tmp_path)).package_install({"names": ["htop"]})
    assert not any(c[0] == "pacman" for c in r.calls)


def test_install_waits_for_keyring(tmp_path):
    with pytest.raises(ActionError, match="keyring is still being set up"):
        Actions(FakeRunner(), keyring=str(tmp_path)).package_install({"names": ["htop"]})


def test_snapshot_undo_only_for_a_pre_post_pair():
    r = FakeRunner()
    assert Actions(r, keyring=KEYRING).snapshot_undo({"pre": 11, "post": 12}) == {"undone": [11, 12]}
    assert ["snapper", "-c", "root", "undochange", "11..12"] in r.calls
    for bad in ({"pre": 11, "post": 13}, {"pre": 12, "post": 11}, {"pre": 11, "post": 99}, {"pre": "11", "post": 12}):
        with pytest.raises(ActionError):
            Actions(FakeRunner(), keyring=KEYRING).snapshot_undo(bad)


def test_snapshot_create_with_garbled_snapper_output():
    class Garbled(FakeRunner):
        def __call__(self, argv):
            if argv[0] == "snapper" and "create" in argv:
                return subprocess.CompletedProcess(argv, 0, "not a number\n", "")
            return super().__call__(argv)

    with pytest.raises(ActionError, match="did not return a snapshot number"):
        Actions(Garbled(), keyring=KEYRING).snapshot_create({})


from exo import crypto
from exo_os.ledger import MemorySink, NullSink
from exo_os.sysd.protocol import MAX_REPLY, MAX_REQUEST, VERSION, decode, encode


def _server(tmp_path, **kwargs):
    return Server(Actions(FakeRunner(), keyring=KEYRING), key_path=tmp_path / 'key',
                  public_key_path=tmp_path / 'receipt.pub', log_path=kwargs.pop('log_path', tmp_path / 'log'), **kwargs)


def _request(action='snapshot.create', args=None):
    return {'version': VERSION, 'request_id': 'a' * 64, 'action': action, 'args': args or {}}


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory(prefix='exo-sysd-', dir='/tmp') as directory:
        yield Path(directory)


@pytest.fixture
def trust_context(monkeypatch, tmp_path):
    monkeypatch.setenv('EXO_HOME', str(tmp_path / 'home'))
    # Explicitly simulate kernel credentials on macOS. Linux integration below uses real credentials.
    if not hasattr(socket, 'SO_PEERCRED'):
        monkeypatch.setattr('exo_os.sysd.client.peer_credentials', lambda c: (os.getpid(), os.getuid(), os.getgid()))
        monkeypatch.setattr('exo_os.sysd.server.peer_credentials', lambda c: (os.getpid(), os.getuid(), os.getgid()))
    sink = MemorySink()
    monkeypatch.setattr('exo_os.sysd.client.open_sink', lambda *a: sink)
    return sink


def _call_server(monkeypatch, socket_dir, srv, transform=lambda reply: reply):
    """Serve exactly one request and close the listener; no leaked background daemons."""
    path = str(socket_dir / 'sysd.sock')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)

    def worker():
        with listener:
            conn, _ = listener.accept()
            with conn:
                f = conn.makefile('rb')
                request = f.readline(MAX_REQUEST + 1)
                reply = srv.handle_line(request, (os.getpid(), os.getuid(), os.getgid()))
                wire = transform(reply)
                try:
                    conn.sendall(wire if isinstance(wire, bytes) else encode(wire))
                except BrokenPipeError:
                    pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return SysdClient(path, timeout=2, expected_peer_uid=os.getuid(), trust_key=crypto.pubkey_b64(srv.key)), thread


def test_server_runs_named_actions_and_signs_receipts(tmp_path, socket_dir, monkeypatch, trust_context):
    srv = _server(tmp_path)
    client, thread = _call_server(monkeypatch, socket_dir, srv)
    assert client.call('snapshot.create', {'description': 'test'}) == {'snapshot': 11}
    thread.join(2)
    assert not thread.is_alive()
    receipt = trust_context.events[0]['receipt']
    assert crypto.verify_doc(receipt, crypto.pubkey_b64(srv.key))
    assert receipt['uid'] == os.getuid() and receipt['pid'] == os.getpid()
    assert decode((tmp_path / 'log').read_bytes()) == receipt
    assert not crypto.verify_doc(dict(receipt, ok=False), crypto.pubkey_b64(srv.key))
    assert json.loads(next((tmp_path / 'home/sysd-outbox').glob('*.json')).read_text()) == receipt
    with pytest.raises(SysdError, match="does not do 'shell'"):
        client.call('shell', {'cmd': 'id'})


@pytest.mark.parametrize('bad', [b'not json', b'[]', b'null', b'{"action": []}',
                                 b'{"action": {}}', b'{"action": true}',
                                 b'{"action":"status","action":"status"}',
                                 b'{"action":"status","args": NaN}', b'[' * 1500,
                                 b' ' * (MAX_REQUEST + 1)])
def test_bad_requests_are_rejected_without_running(tmp_path, bad):
    srv = _server(tmp_path)
    assert srv.handle_line(bad, (1, 1000, 1000)) == {'ok': False, 'error': 'bad request'}
    assert srv.actions.runner.calls == []


def test_malformed_known_action_and_args(tmp_path):
    srv = _server(tmp_path)
    for patch in ({'action': []}, {'action': {}}, {'args': []}, {'version': True}, {'request_id': 'replay'}):
        assert srv.handle_line(encode(_request() | patch), (1, 1000, 1000))['error'] == 'bad request'
    refused = srv.handle_line(encode(_request('package.install', {'names': ['-Syu']})), (1, 1000, 1000))
    assert not refused['ok'] and crypto.verify_doc(refused['receipt'], crypto.pubkey_b64(srv.key))
    assert srv.actions.runner.calls == []


@pytest.mark.parametrize('attack', ['missing', 'forged', 'tampered', 'action', 'args', 'result',
                                    'ok', 'error', 'uid', 'pid', 'replay', 'version', 'audit_error'])
def test_untrusted_receipt_rejected(tmp_path, socket_dir, monkeypatch, trust_context, attack):
    srv = _server(tmp_path)

    def alter(reply):
        if attack == 'missing':
            del reply['receipt']
            return reply
        doc = reply['receipt']
        if attack == 'forged':
            rogue = crypto.keygen()
            doc['pub'] = crypto.pubkey_b64(rogue)
            reply['receipt'] = crypto.sign_doc(doc, rogue)
        elif attack == 'tampered':
            doc['result'] = {'snapshot': 999}
        else:
            # Even correctly signed receipts cannot attest another request or reply.
            patch = {'action': 'snapshot.undo', 'args': {'description': 'other'}, 'result': {'snapshot': 999},
                     'ok': False, 'error': 'invented', 'uid': os.getuid() + 1, 'pid': os.getpid() + 1,
                     'replay': 'f' * 64, 'version': 99, 'audit_error': 'invented'}
            doc['request_id' if attack == 'replay' else attack] = patch[attack]
            reply['receipt'] = crypto.sign_doc(doc, srv.key)
        return reply

    client, thread = _call_server(monkeypatch, socket_dir, srv, alter)
    with pytest.raises(SysdError, match='Untrusted system helper receipt'):
        client.call('snapshot.create')
    thread.join(2)
    assert trust_context.events == []
    assert not list((tmp_path / 'home/sysd-outbox').glob('*.json'))


@pytest.mark.parametrize('reply', [b'{broken}\n', b'[]\n', b'{}\n', b'{"ok":1}\n', b' ' * (MAX_REPLY + 1),
                                   b'{}', b'{"ok":true,"ok":false}\n'])
def test_corrupt_and_oversize_replies_are_bounded(tmp_path, socket_dir, monkeypatch, trust_context, reply):
    client, thread = _call_server(monkeypatch, socket_dir, _server(tmp_path), lambda r: reply)
    with pytest.raises(SysdError):
        client.call('status')
    thread.join(2)
    assert not thread.is_alive()
    assert trust_context.events == []


def test_peer_mismatch_refuses_before_send(monkeypatch):
    calls = []

    class FakeSock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): pass
        def connect(self, path): calls.append('connect')
        def sendall(self, wire): calls.append('sent')

    monkeypatch.setattr('exo_os.sysd.client.socket.socket', FakeSock)
    monkeypatch.setattr('exo_os.sysd.client.peer_credentials', lambda c: (123, 1000, 1000))
    monkeypatch.setenv('EXO_SYSD_PEER_UID', '1000')
    with pytest.raises(SysdError, match='peer uid 1000 is not trusted'):
        SysdClient('fake').call('snapshot.create')
    assert calls == ['connect']


def test_trust_anchor_failure_refuses_reply_before_ledger(monkeypatch):
    calls = []

    class FakeSock:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): pass
        def connect(self, path): pass
        def sendall(self, wire): calls.append('sent')
        def recv(self, size): return b'{"ok": true}\n'

    monkeypatch.setattr('exo_os.sysd.client.socket.socket', FakeSock)
    monkeypatch.setattr('exo_os.sysd.client.peer_credentials', lambda c: (123, 0, 0))
    monkeypatch.setattr('exo_os.sysd.client._trust_key', lambda path: (_ for _ in ()).throw(ValueError('unsafe owner')))
    monkeypatch.setenv('EXO_SYSD_TRUST_KEY', crypto.pubkey_b64(crypto.keygen()))
    with pytest.raises(SysdError, match='unsafe owner'):
        SysdClient('fake').call('status')
    assert calls == ['sent']


@pytest.mark.parametrize('sink_failure', ['exception', 'null'])
def test_completed_action_ledger_failure_has_durable_receipt(tmp_path, socket_dir, monkeypatch, trust_context, sink_failure):
    if sink_failure == 'exception':
        monkeypatch.setattr('exo_os.sysd.client.open_sink', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    else:
        monkeypatch.setattr('exo_os.sysd.client.open_sink', lambda *a: NullSink())
    srv = _server(tmp_path)
    client, thread = _call_server(monkeypatch, socket_dir, srv)
    with pytest.raises(CompletedButUnrecorded, match='Action completed; receipt not recorded') as caught:
        client.call('snapshot.create')
    thread.join(2)
    outcome = caught.value.to_dict()
    assert outcome['completed'] is True and outcome['retryable'] is False
    assert outcome['result'] == {'snapshot': 11}
    assert json.loads(Path(outcome['outbox_path']).read_text()) == outcome['receipt']
    assert crypto.verify_doc(outcome['receipt'], crypto.pubkey_b64(srv.key))
    assert len(srv.actions.runner.calls) == 1


def test_root_log_preflight_refuses_action(tmp_path):
    srv = _server(tmp_path, log_path=tmp_path / 'missing/log')
    reply = srv.handle_line(encode(_request()), (1, 1000, 1000))
    assert not reply['ok'] and 'action was not run' in reply['error']
    assert srv.actions.runner.calls == []
    assert crypto.verify_doc(reply['receipt'], crypto.pubkey_b64(srv.key))


def test_root_log_failure_after_action_retains_completed_outcome(tmp_path, socket_dir, monkeypatch, trust_context):
    srv = _server(tmp_path)

    class FailingLog:
        def write(self, wire): raise OSError('disk full')
        def close(self): pass

    monkeypatch.setattr(srv, '_open_log', lambda: FailingLog())
    client, thread = _call_server(monkeypatch, socket_dir, srv)
    with pytest.raises(CompletedButUnrecorded) as caught:
        client.call('snapshot.create')
    thread.join(2)
    assert caught.value.result == {'snapshot': 11}
    assert caught.value.receipt['audit_error'] == 'root audit log append failed'
    assert caught.value.outbox_path.exists()
    assert crypto.verify_doc(caught.value.receipt, crypto.pubkey_b64(srv.key))
    assert len(srv.actions.runner.calls) == 1


def test_key_publication_preserves_signing_key(tmp_path):
    srv = _server(tmp_path)
    public = tmp_path / 'receipt.pub'
    assert public.read_text().strip() == crypto.pubkey_b64(srv.key)
    assert public.stat().st_mode & 0o777 == 0o644
    assert (tmp_path / 'key').stat().st_mode & 0o777 == 0o600
    assert public.read_text() != (tmp_path / 'key').read_text()
    again = _server(tmp_path)
    assert crypto.pubkey_b64(again.key) == public.read_text().strip()


def test_mutations_cannot_silently_skip_audit():
    with pytest.raises(SysdError, match='require receipt recording'):
        SysdClient('unused').call('snapshot.create', record=False)


def test_unreachable_helper(socket_dir):
    with pytest.raises(SysdError, match='not reachable'):
        SysdClient(str(socket_dir / 'none')).call('status')


def test_server_read_timeout_closes_idle_connection(tmp_path, monkeypatch):
    srv = _server(tmp_path, io_timeout=0.03)
    monkeypatch.setattr('exo_os.sysd.server.peer_credentials', lambda c: (os.getpid(), os.getuid(), os.getgid()))
    left, right = socket.socketpair()
    worker = threading.Thread(target=srv._client, args=(left,))
    worker.start()
    worker.join(1)
    assert not worker.is_alive()
    assert right.recv(1) == b''
    right.close()
    assert srv.actions.runner.calls == []


def test_recovery_records_receipt_once_without_running_action(tmp_path, socket_dir, monkeypatch, trust_context):
    srv = _server(tmp_path)
    sink = trust_context
    monkeypatch.setattr('exo_os.sysd.client.open_sink', lambda *a: NullSink())
    client, thread = _call_server(monkeypatch, socket_dir, srv)
    with pytest.raises(CompletedButUnrecorded) as caught:
        client.call('snapshot.create')
    thread.join(2)
    request_id = caught.value.receipt['request_id']
    monkeypatch.setattr('exo_os.sysd.client.open_sink', lambda *a: sink)
    # A socket that cannot be used proves reconciliation is exclusively an audit operation.
    monkeypatch.setattr('exo_os.sysd.client.socket.socket', lambda *a: (_ for _ in ()).throw(AssertionError('must not connect')))
    first = client.recover_pending()
    assert first == {'recorded': [request_id], 'already_recorded': [], 'rejected': [], 'errors': [], 'root_audit_errors': []}
    second = client.recover_pending()
    assert second['already_recorded'] == [request_id] and second['recorded'] == []
    assert len(sink.events) == 1 and len(srv.actions.runner.calls) == 1
    assert caught.value.outbox_path.exists()


def test_recovery_rejects_forged_tampered_and_relocated_receipts(tmp_path, monkeypatch, trust_context):
    from exo_os.sysd.client import _save_receipt, _outbox_dir

    srv = _server(tmp_path)
    legitimate = srv.handle_line(encode(_request()), (os.getpid(), os.getuid(), os.getgid()))['receipt']
    directory = _outbox_dir()
    forged = dict(legitimate, request_id='b' * 64)
    rogue = crypto.keygen()
    forged = crypto.sign_doc(dict(forged, pub=crypto.pubkey_b64(rogue)), rogue)
    _save_receipt(directory, forged)
    _save_receipt(directory, dict(legitimate, result={'snapshot': 888}))
    different_uid = crypto.sign_doc(dict(legitimate, request_id='c' * 64, uid=os.getuid() + 1), srv.key)
    _save_receipt(directory, different_uid)
    moved = directory / ('d' * 64 + '.json')
    moved.write_bytes(encode(legitimate))
    moved.chmod(0o600)
    summary = SysdClient(trust_key=crypto.pubkey_b64(srv.key)).recover_pending()
    assert len(summary['rejected']) == 4 and summary['recorded'] == [] and summary['errors'] == []
    assert trust_context.events == []


def test_recovery_ignores_forged_ledger_dedupe_marker(tmp_path, monkeypatch, trust_context):
    from exo_os.sysd.client import _save_receipt, _outbox_dir

    srv = _server(tmp_path)
    receipt = srv.handle_line(encode(_request()), (os.getpid(), os.getuid(), os.getgid()))['receipt']
    _save_receipt(_outbox_dir(), receipt)
    trust_context.append('system.action', {'receipt': dict(receipt, result={'snapshot': 999})})
    summary = SysdClient(trust_key=crypto.pubkey_b64(srv.key)).recover_pending()
    assert summary['recorded'] == [receipt['request_id']] and summary['already_recorded'] == []
    assert len(trust_context.events) == 2


def test_recovery_keeps_receipt_on_ledger_outage(tmp_path, monkeypatch, trust_context):
    from exo_os.sysd.client import _save_receipt, _outbox_dir

    srv = _server(tmp_path)
    receipt = srv.handle_line(encode(_request()), (os.getpid(), os.getuid(), os.getgid()))['receipt']
    saved = _save_receipt(_outbox_dir(), receipt)
    monkeypatch.setattr('exo_os.sysd.client.open_sink', lambda *a: NullSink())
    summary = SysdClient(trust_key=crypto.pubkey_b64(srv.key)).recover_pending()
    assert summary['errors'] and summary['recorded'] == [] and saved.exists()


def test_real_ledger_dedupe_works(tmp_path, monkeypatch, trust_context):
    from exo_os.sysd.client import _save_receipt, _outbox_dir
    from exo_os.ledger import open_sink

    store = tmp_path / 'store'
    store.mkdir()
    monkeypatch.setenv('EXO_PRINCIPAL_STORE', str(store))
    monkeypatch.setattr('exo_os.sysd.client.open_sink', open_sink)
    srv = _server(tmp_path)
    receipt = srv.handle_line(encode(_request()), (os.getpid(), os.getuid(), os.getgid()))['receipt']
    _save_receipt(_outbox_dir(), receipt)
    client = SysdClient(trust_key=crypto.pubkey_b64(srv.key))
    assert client.recover_pending()['recorded'] == [receipt['request_id']]
    assert client.recover_pending()['already_recorded'] == [receipt['request_id']]
    ledger = open_sink(store, tmp_path / 'home').ledger
    assert len(list(ledger.iter(kind='action'))) == 1


@pytest.mark.parametrize('names', [['htop\n'], ['foot\r'], ['-htop']])
def test_package_names_cannot_contain_line_terminators_or_options(names):
    runner = FakeRunner()
    with pytest.raises(ActionError, match='not a package name'):
        Actions(runner, keyring=KEYRING).package_install({'names': names})
    assert runner.calls == []


@pytest.mark.parametrize('description', ['test\n', [], {}, True, 123])
def test_snapshot_description_requires_plain_checked_text(description):
    runner = FakeRunner()
    with pytest.raises(ActionError):
        Actions(runner).snapshot_create({'description': description})
    assert runner.calls == []


def test_server_thread_slots_are_released_on_peer_error(tmp_path, monkeypatch):
    srv = _server(tmp_path, max_connections=1)
    monkeypatch.setattr('exo_os.sysd.server.peer_credentials', lambda c: (_ for _ in ()).throw(OSError('bad peer')))
    assert srv.slots.acquire(blocking=False)
    left, right = socket.socketpair()
    srv._client_slot(left)
    assert srv.slots.acquire(blocking=False)
    srv.slots.release()
    right.close()


def test_server_request_oversize_closes_without_action(tmp_path, monkeypatch):
    srv = _server(tmp_path)
    monkeypatch.setattr('exo_os.sysd.server.peer_credentials', lambda c: (os.getpid(), os.getuid(), os.getgid()))
    left, right = socket.socketpair()
    worker = threading.Thread(target=srv._client, args=(left,))
    worker.start()
    right.sendall(b' ' * (MAX_REQUEST + 1))
    worker.join(2)
    assert not worker.is_alive() and right.recv(1) == b''
    right.close()
    assert srv.actions.runner.calls == []


def test_first_socket_activation_loads_key_after_helper_publishes(tmp_path, monkeypatch, trust_context):
    srv = _server(tmp_path)
    calls = []

    class FakeSock:
        def __init__(self, *a): self.wire = b''
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def settimeout(self, value): pass
        def connect(self, path): calls.append('connect')
        def sendall(self, wire):
            calls.append('sent')
            self.wire = encode(srv.handle_line(wire, (os.getpid(), os.getuid(), os.getgid())))
        def recv(self, size):
            calls.append('received')
            chunk, self.wire = self.wire[:size], self.wire[size:]
            return chunk

    def key_after_start(path):
        assert 'received' in calls  # no key exists during first connect/send
        calls.append('key_loaded')
        return crypto.pubkey_b64(srv.key)

    monkeypatch.setattr('exo_os.sysd.client.socket.socket', FakeSock)
    monkeypatch.setattr('exo_os.sysd.client.peer_credentials', lambda c: calls.append('root_authenticated') or (123, 0, 0))
    monkeypatch.setattr('exo_os.sysd.client._trust_key', key_after_start)
    assert SysdClient('activated').call('snapshot.create') == {'snapshot': 11}
    assert calls.index('root_authenticated') < calls.index('sent') < calls.index('key_loaded')
    assert len(trust_context.events) == 1


def test_receipt_duration_survives_wall_clock_correction(tmp_path, monkeypatch):
    srv = _server(tmp_path)
    monkeypatch.setattr('exo_os.sysd.server.time.time', lambda: -100000)
    reply = srv.handle_line(encode(_request()), (os.getpid(), os.getuid(), os.getgid()))
    assert reply['ok'] and reply['receipt']['ms'] >= 0
    from exo_os.sysd.client import _valid_receipt
    assert _valid_receipt(reply['receipt'], crypto.pubkey_b64(srv.key))


@pytest.mark.parametrize('fault', ['directory_writable', 'directory_owner', 'file_writable', 'file_owner', 'symlink'])
def test_pinned_anchor_rejects_unsafe_ownership_modes_and_symlinks(tmp_path, monkeypatch, fault):
    import stat
    from types import SimpleNamespace
    from exo_os.sysd.client import _trust_key

    key = tmp_path / 'anchor.pub'
    key.write_text(crypto.pubkey_b64(crypto.keygen()))
    original_lstat = Path.lstat
    original_fstat = os.fstat

    def lstat(path):
        if path == key:
            return original_lstat(path)
        mode, uid = stat.S_IFDIR | 0o755, 0
        if path == tmp_path:
            if fault == 'directory_writable': mode |= 0o020
            if fault == 'directory_owner': uid = 1234
        return SimpleNamespace(st_mode=mode, st_uid=uid)

    def fstat(fd):
        meta = original_fstat(fd)
        return SimpleNamespace(st_mode=(meta.st_mode | (0o020 if fault == 'file_writable' else 0)),
                               st_uid=1234 if fault == 'file_owner' else 0)

    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(os, 'fstat', fstat)
    if fault == 'symlink':
        linked = tmp_path / 'linked.pub'
        linked.symlink_to(key)
        key = linked
    with pytest.raises((ValueError, OSError)):
        _trust_key(key)


def test_off_linux_peer_authentication_fails_closed(monkeypatch):
    from exo_os.sysd.protocol import peer_credentials

    monkeypatch.delattr(socket, 'SO_PEERCRED', raising=False)
    with pytest.raises(OSError, match='requires Linux SO_PEERCRED'):
        peer_credentials(object())


def test_server_rejects_excess_connections_without_new_threads(tmp_path, monkeypatch):
    srv = _server(tmp_path, max_connections=1)
    started = []
    closed = []

    class Connection:
        def __init__(self, n): self.n = n
        def close(self): closed.append(self.n)

    class Listener:
        def __init__(self): self.pending = iter([Connection(n) for n in range(3)])
        def accept(self): return next(self.pending), None

    class Thread:
        def __init__(self, **kwargs): self.conn = kwargs['args'][0]
        def start(self): started.append(self.conn.n)  # first worker remains occupied

    monkeypatch.setattr('exo_os.sysd.server.threading.Thread', Thread)
    with pytest.raises(StopIteration):
        srv.serve(Listener())
    assert started == [0] and closed == [1, 2]

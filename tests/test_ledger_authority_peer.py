"""Authenticate kernel identity before sending intents; actual Linux tests use fixture sockets only."""
import os
import socket
import sys
import types

import pytest

from exo import crypto
from exo.ledger_authority import AppendOutcomeUnknown, AuthorityUnavailable
from exo_os.ledgerd import client
from exo_os.ledgerd.wire import GREETING
from exo_os.sysd.protocol import peer_credentials


def identity():
    return {"version": 1, "stream_id": "fixture", "checkpoint_pub": crypto.pubkey_b64(crypto.keygen()),
            "writers": {"exo-os": {"pub": crypto.pubkey_b64(crypto.keygen()), "author": "exo-os", "authors": ["exo-os"]}}}


@pytest.mark.parametrize("stage", ["socket", "listener", "service", "reply_lost"])
def test_no_payload_until_listener_and_service_authenticated(monkeypatch, stage):
    seen = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def settimeout(self, *a):
            pass
        def setsockopt(self, *a):
            pass
        def connect(self, *a):
            seen.append("connect")
        def sendall(self, data):
            seen.append("payload")
    monkeypatch.setattr(client, "socket", types.SimpleNamespace(
        AF_UNIX=1, SOCK_STREAM=1, SOL_SOCKET=1, SO_PASSCRED=1, socket=lambda *a: Connection()))
    monkeypatch.setattr(client, "account_uid", lambda name: 0 if name == "root" else 777)
    def verify_socket(*a):
        if stage == "socket":
            raise AuthorityUnavailable("socket mismatch")
    def verify_service(*a):
        if stage == "service":
            raise AuthorityUnavailable("service mismatch")
    monkeypatch.setattr(client, "verify_socket", verify_socket)
    monkeypatch.setattr(client, "peer_credentials", lambda conn: (1, 0 if stage != "listener" else 999, 0))
    monkeypatch.setattr(client, "verify_greeting", verify_service)
    monkeypatch.setattr(client, "read_line", lambda *a: (_ for _ in ()).throw(OSError("reply lost")))
    bound = client.AuthorityClient("fixture", "exo-os", identity())
    error = AppendOutcomeUnknown if stage == "reply_lost" else AuthorityUnavailable
    with pytest.raises(error):
        bound._call("append", operation_id="same-id", expected_head={"seq": 0, "chain": crypto.GENESIS},
                    intent={"kind": "action", "author": "exo-os", "body": {}, "session": None, "refs": []})
    assert ("payload" in seen) == (stage == "reply_lost")


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0,
                    reason="requires a disposable root Linux runner; no host paths")
def test_root_listener_and_actual_unprivileged_signer_credentials(socket_dir):
    import pwd
    uid = pwd.getpwnam("nobody").pw_uid
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = str(socket_dir / "authority.sock")
    listener.bind(path)
    os.chmod(path, 0o666)
    listener.listen(1)
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            conn, _ = listener.accept()
            conn.sendall(GREETING)
            assert conn.recv(4) == b"okay"
            os._exit(0)
        except BaseException:
            os._exit(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            conn.connect(path)
            assert peer_credentials(conn)[1] == 0
            client.verify_greeting(conn, uid)
            conn.sendall(b"okay")
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        listener.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux credential ancillary data")
def test_service_uid_mismatch_rejects_greeting_before_payload(socket_dir):
    import threading
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = str(socket_dir / "wrong.sock")
    listener.bind(path)
    listener.listen(1)
    received = []
    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.sendall(GREETING)
            received.append(conn.recv(1))
    worker = threading.Thread(target=serve)
    worker.start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        conn.connect(path)
        with pytest.raises(AuthorityUnavailable):
            client.verify_greeting(conn, os.getuid() + 1)
    worker.join(3)
    listener.close()
    assert received == [b""]

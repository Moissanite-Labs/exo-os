"""Protocol auth simulations on every host; inherited listener kernel check on root Linux."""
import array
import os
import socket
import struct
import sys

import pytest

from exo_os.egress.client import verify_greeting
from exo_os.egress.policy import EgressError
from exo_os.sysd.protocol import peer_credentials


@pytest.fixture
def credentials_type(monkeypatch):
    monkeypatch.setattr(socket, "SCM_CREDENTIALS", getattr(socket, "SCM_CREDENTIALS", 2), raising=False)
    return socket.SCM_CREDENTIALS


class FakeGreeting:
    def __init__(self, ancillary, flags=0, data=b"exo-egress-v1\n"):
        self.ancillary, self.flags, self.data = ancillary, flags, data
    def settimeout(self, timeout):
        pass
    def recvmsg(self, *args):
        return self.data, self.ancillary, self.flags, None


def test_greeting_accepts_only_expected_sender(credentials_type):
    good = [(socket.SOL_SOCKET, credentials_type, struct.pack("3i", 123, 777, 777))]
    verify_greeting(FakeGreeting(good), 777)
    for ancillary, flags in ((good, socket.MSG_CTRUNC), ([], 0), (good + good, 0),
                              ([(socket.SOL_SOCKET, credentials_type, b"short")], 0),
                              ([(socket.SOL_SOCKET, 9999, b"")], 0)):
        with pytest.raises(EgressError):
            verify_greeting(FakeGreeting(ancillary, flags), 777)
    with pytest.raises(EgressError):
        verify_greeting(FakeGreeting(good), 778)


def test_unexpected_descriptors_closed(credentials_type):
    read_fd, write_fd = os.pipe()
    good = [(socket.SOL_SOCKET, credentials_type, struct.pack("3i", 123, 777, 777)),
            (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [read_fd]).tobytes())]
    try:
        with pytest.raises(EgressError):
            verify_greeting(FakeGreeting(good), 777)
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0,
                    reason="actual inherited-listener credentials require disposable root Linux runner")
def test_root_created_inherited_listener_reports_actual_service_uid(socket_dir):
    import pwd
    service_uid = pwd.getpwnam("nobody").pw_uid
    path = str(socket_dir / "peer.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    os.chmod(path, 0o666)
    listener.listen(1)
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups([])
            os.setgid(service_uid)
            os.setuid(service_uid)
            conn, _ = listener.accept()
            conn.sendall(b"exo-egress-v1\n")
            assert conn.recv(4) == b"okay"
            conn.close()
            os._exit(0)
        except BaseException:  # noqa: BLE001 -- child must terminate without unwinding parent pytest
            os._exit(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            client.connect(path)
            assert peer_credentials(client)[1] == 0  # inherited root creator, NOT service UID
            verify_greeting(client, service_uid)
            client.sendall(b"okay")
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        listener.close()


def test_client_checks_identity_before_send_and_lost_reply_never_retries(monkeypatch):
    import types

    from exo_os.egress import client

    calls = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, timeout):
            pass
        def setsockopt(self, *args):
            pass
        def connect(self, path):
            pass
        def sendall(self, payload):
            calls.append(payload)
    monkeypatch.setattr(client, "socket", types.SimpleNamespace(
        AF_UNIX=1, SOCK_STREAM=1, SOL_SOCKET=1, SO_PASSCRED=1, socket=lambda *args: Connection()))
    monkeypatch.setattr(client, "verify_socket", lambda *args: None)
    monkeypatch.setattr(client, "account_uid", lambda name: 0 if name == "root" else 777)
    monkeypatch.setattr(client, "peer_credentials", lambda conn: (123, 111, 111))
    monkeypatch.setattr(client, "verify_greeting", lambda *args: None)
    args = {"profile": "fixture", "expected_origin": "https://fixture.example", "request_id": "stable",
            "method": "POST", "path": "/api/action", "body": "{}"}
    result = client.EgressClient().call(args)
    assert result["state"] == "refused" and calls == []
    monkeypatch.setattr(client, "peer_credentials", lambda conn: (1, 0, 0))
    def lost(*args):
        raise OSError("fixture lost reply")
    monkeypatch.setattr(client, "read_line", lost)
    result = client.EgressClient().call(args)
    assert result["state"] == "unknown" and result["retryable"] is False and result["request_id"] == "stable"
    assert len(calls) == 1

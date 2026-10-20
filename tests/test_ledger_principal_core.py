"""OS-21 (issue #106): only the core process its launch record names may append as the principal writer.

A real Unix listener runs the real signer `Server` against a real journal. The test process is the core: it
writes its launch record with the pinned core's own `exo.principal.issue`. Another process of the same user
is a separate interpreter. On Linux the signer reads the kernel peer with SO_PEERCRED as in the image; on
macOS the same Server runs with the core's LOCAL_PEERCRED reading in its place, since the signer ships only for
Linux.
"""
from __future__ import annotations

import array
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from exo import crypto
from exo import principal as core
from exo.ensure_core import exod_socket
from exo.ledger_authority import AppendOutcomeUnknown, AuthorityError, verify_record
from exo.ledger_authority.journal import AuthorityJournal
from exo_os.ledgerd import client, policy, server, wire
from exo_os.sysd.protocol import read_line

pytestmark = pytest.mark.skipif(sys.platform not in ("linux", "darwin"),
                                reason="kernel peer identity and process start identity: Linux and macOS only")

WRITERS = ("principal", "exo-os")

# Another process of the same user: it connects, reads the greeting and sends one append, with the record's
# descriptor when asked. Given the core's socket, it first writes a launch record that names itself. It uses only
# the wire format, not the exo-os client.
OTHER = r"""
import array, json, os, socket, sys
from exo import principal
from exo_os.ledgerd import wire
from exo_os.sysd.protocol import read_line
path, raw, record = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
if len(sys.argv) > 4:
    principal.issue(sys.argv[4])
fds = [os.open(record, os.O_RDONLY)] if record else []
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
    conn.settimeout(10)
    conn.connect(path)
    got = b""
    while len(got) < len(wire.GREETING):
        chunk = conn.recv(len(wire.GREETING) - len(got))
        if not chunk:
            raise SystemExit("no greeting")
        got += chunk
    data = wire.encode(raw)
    if fds:
        conn.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))])
    else:
        conn.sendall(data)
    print(json.dumps(wire.decode(read_line(conn, wire.MAX_WIRE, 10))))
"""


def kernel_peer(conn):
    pid, uid = core.peer_cred(conn)
    return pid, uid, 0


@pytest.fixture
def authority(socket_dir, monkeypatch):
    root = socket_dir.resolve()  # the kernel names a descriptor's path with no symlink (macOS /tmp)
    home = root / "home"
    (home / "principals" / "exo").mkdir(parents=True, mode=0o700)
    keys = {writer: crypto.keygen() for writer in WRITERS}
    checkpoint = crypto.keygen()
    identity = {"version": 1, "stream_id": "fixture", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {w: {"pub": crypto.pubkey_b64(keys[w]), "author": w, "authors": [w]} for w in WRITERS}}
    journal = AuthorityJournal.provision(root / "journal.sqlite", identity, keys, checkpoint)
    ledger_path = home / "principals" / "exo" / "ledger.sqlite"
    manifest = {"version": 1, "streams": {"fixture": {"ledger_path": str(ledger_path), "identity": identity,
                                                      "callers": {w: ["exo"] for w in WRITERS}}}}
    if not hasattr(socket, "SO_PEERCRED"):
        monkeypatch.setattr(server, "peer_credentials", kernel_peer)
    signer = server.Server(manifest, {"fixture": journal}, {"exo": os.geteuid()})
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = str(root / "ledger.sock")
    listener.bind(path)
    listener.listen(8)
    listener.settimeout(0.1)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            conn.settimeout(None)
            signer.serve_connection(conn)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    sock = exod_socket(home)
    yield SimpleNamespace(path=path, home=home, journal=journal, identity=identity, manifest=manifest,
                          ledger_path=ledger_path, core_socket=sock, record=core.token_path(sock))
    stop.set()
    worker.join(5)
    listener.close()


@pytest.fixture
def reasons(monkeypatch):
    """Why the signer refused the core binding, so each case is refused for its own reason."""
    seen = []
    original = getattr(server.Server, "bind_core", None)  # absent before OS-21, where these tests fail on the reply

    def bind_core(self, *args):
        try:
            return original(self, *args)
        except AuthorityError as exc:
            seen.append(str(exc))
            raise

    monkeypatch.setattr(server.Server, "bind_core", bind_core, raising=False)
    return seen


def after_record(monkeypatch, hook):
    """Run ``hook`` in the signer after it has read the launch record and before it checks the record's process."""
    original = server.core_record

    def read_then_hook(*args):
        record = original(*args)
        hook()
        return record

    monkeypatch.setattr(server, "core_record", read_then_hook)


def append_request(writer, operation, head):
    return {"version": 1, "nonce": "n" + operation, "stream": "fixture", "writer": writer, "op": "append",
            "operation_id": operation, "expected_head": {"seq": head["seq"], "chain": head["chain"]},
            "intent": {"author": writer, "kind": "note", "body": {"probe": operation}, "session": None, "refs": []}}


def send(path, raw, fds=()):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(10)
        conn.connect(path)
        got = b""
        while len(got) < len(wire.GREETING):
            chunk = conn.recv(len(wire.GREETING) - len(got))
            assert chunk, "no greeting"
            got += chunk
        data = wire.encode(raw)
        if fds:
            conn.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))])
        else:
            conn.sendall(data)
        return wire.decode(read_line(conn, wire.MAX_WIRE, 10))


def other_process(authority, raw, record=""):
    result = subprocess.run([sys.executable, "-c", OTHER, authority.path, json.dumps(raw), str(record)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def as_core(authority, raw, *paths):
    fds = [os.open(path, os.O_RDONLY) for path in paths]
    try:
        return send(authority.path, raw, fds)
    finally:
        for fd in fds:
            os.close(fd)


def unchanged(authority, head, operation, writer="principal"):
    assert authority.journal.head()["seq"] == head["seq"]
    assert authority.journal.lookup(operation, writer=writer) is None


NOT_USER = "launch record names another user"
NOT_PROCESS = "launch record names another process"
NOT_START = "launch record names another process start"
GONE = "the process that connected has ended or its pid was reused"
NOT_PRIVATE = "descriptor is not the core's private launch record"
NOT_CURRENT = "descriptor is not the core's current launch record"


@pytest.mark.parametrize("with_record", [False, True], ids=["bare", "with-genuine-record"])
def test_another_process_of_the_desktop_user_cannot_append_as_principal(authority, reasons, with_record):
    """The probe for #106. Before OS-21 the signer accepted this from any process of the caller UID."""
    core.issue(authority.core_socket)  # this test process is the core its launch record names
    head = authority.journal.head()
    reply = other_process(authority, append_request("principal", "forged", head),
                          authority.record if with_record else "")
    assert reply["state"] == "refused" and reply["result"] is None, reply
    unchanged(authority, head, "forged")
    assert reasons == [NOT_PROCESS if with_record else "this writer appends only with the core's launch record"]


def test_the_core_named_by_its_launch_record_appends_as_principal(authority):
    core.issue(authority.core_socket)
    head = authority.journal.head()
    reply = as_core(authority, append_request("principal", "spoken", head), authority.record)
    assert reply["state"] == "ok", reply
    record = reply["result"]
    verify_record(authority.identity, record, head)
    assert record["checkpoint"]["writer"] == "principal" and record["event"]["author"] == "principal"
    assert authority.journal.lookup("spoken", writer="principal") == record


def test_other_writers_keep_the_uid_rule(authority):
    head = authority.journal.head()
    reply = other_process(authority, append_request("exo-os", "os-event", head))
    assert reply["state"] == "ok" and reply["result"]["checkpoint"]["writer"] == "exo-os"
    # Reads of the principal writer's stream stay open to the caller UID; only its appends are bound.
    reply = other_process(authority, {"version": 1, "nonce": "r", "stream": "fixture", "writer": "principal",
                                      "op": "head", "seq": None})
    assert reply["state"] == "ok" and reply["result"]["seq"] == head["seq"] + 1


def test_a_descriptor_on_another_writers_request_refuses(authority):
    core.issue(authority.core_socket)
    head = authority.journal.head()
    reply = as_core(authority, append_request("exo-os", "with-fd", head), authority.record)
    assert reply["state"] == "refused"
    unchanged(authority, head, "with-fd", "exo-os")


def test_record_naming_another_live_process_refuses(authority, reasons):
    """The record names a separate core; the test process holds it but is not that process."""
    other = subprocess.Popen([sys.executable, "-c", "import sys, time\nfrom exo import principal\n"
                              "principal.issue(sys.argv[1]); print('ready', flush=True); time.sleep(60)",
                              str(authority.core_socket)], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "ready"
        assert json.loads(authority.record.read_text())["pid"] == other.pid
        head = authority.journal.head()
        reply = as_core(authority, append_request("principal", "not-me", head), authority.record)
        assert reply["state"] == "refused"
        unchanged(authority, head, "not-me")
        assert reasons == [NOT_PROCESS]
    finally:
        other.kill()
        other.wait()


CASES = {"no-descriptor": "this writer appends only with the core's launch record",
         "two-descriptors": None,  # refused while reading the request, before the binding
         "copy-elsewhere": NOT_CURRENT, "group-readable": NOT_PRIVATE, "hard-link": NOT_PRIVATE,
         "wrong-start": NOT_START, "wrong-uid": NOT_USER, "not-v1": "launch record is not a v1 record",
         # This process's own start identity under another pid: on Linux a process started in the same clock tick
         # has the same start identity, so only the pid sets the core apart.
         "wrong-pid": NOT_PROCESS,
         "pipe": NOT_CURRENT, "other-device": NOT_PRIVATE}


@pytest.mark.parametrize("case", list(CASES))
def test_the_launch_record_must_be_the_cores_own(authority, reasons, monkeypatch, case):
    core.issue(authority.core_socket)
    head = authority.journal.head()
    raw = append_request("principal", "edge-" + case, head)
    record = json.loads(authority.record.read_text())
    paths = [authority.record]
    if case == "no-descriptor":
        paths = []
    elif case == "two-descriptors":
        paths = [authority.record, authority.record]
    elif case == "copy-elsewhere":
        copy = authority.record.with_name("copy.principal.key")
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, authority.record.read_bytes())
        os.close(fd)
        paths = [copy]
    elif case == "group-readable":
        os.chmod(authority.record, 0o640)
    elif case == "hard-link":  # the kernel still names the record's path, but the file has a second name
        os.link(authority.record, authority.record.with_name("link.principal.key"))
    elif case in ("wrong-start", "wrong-uid", "not-v1", "wrong-pid"):
        changed = {"wrong-start": {"start": record["start"] + "0"}, "wrong-uid": {"uid": record["uid"] + 1},
                   "not-v1": {"v": 2}, "wrong-pid": {"pid": record["pid"] + 1}}[case]
        authority.record.write_text(json.dumps({**record, **changed}))
        os.chmod(authority.record, 0o600)
    elif case == "other-device":
        monkeypatch.setattr(server, "store_device", lambda home: os.stat(home).st_dev + 1)
    if case == "pipe":
        read, write = os.pipe()
        try:
            reply = send(authority.path, raw, [read])
        finally:
            os.close(read)
            os.close(write)
    else:
        reply = as_core(authority, raw, *paths)
    assert reply["state"] == "refused", case
    unchanged(authority, head, raw["operation_id"])
    assert reasons == ([CASES[case]] if CASES[case] else [])


def test_a_process_that_ends_before_the_check_refuses(authority, reasons, monkeypatch):
    """The record names the process that connected, and that process ends while the signer reads the record. Its
    pid no longer names it when the signer checks, so the append refuses."""
    reached, proceed = threading.Event(), threading.Event()
    after_record(monkeypatch, lambda: (reached.set(), proceed.wait(30)))
    head = authority.journal.head()
    raw = append_request("principal", "ended", head)
    child = subprocess.Popen([sys.executable, "-c", OTHER, authority.path, json.dumps(raw), str(authority.record),
                              str(authority.core_socket)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert reached.wait(30), "the signer never read the child's launch record"
        assert json.loads(authority.record.read_text())["pid"] == child.pid
    finally:
        child.kill()
        child.wait()  # reaped: its pid names no process now
        proceed.set()
    # The listener serves one connection at a time, so this reply comes after the child's request is done.
    reply = send(authority.path, {"version": 1, "nonce": "h", "stream": "fixture", "writer": "principal",
                                  "op": "head", "seq": None})
    assert reply["state"] == "ok"
    unchanged(authority, head, "ended")
    assert reasons == [GONE]


def test_a_pid_reused_before_the_check_refuses(authority, reasons, monkeypatch):
    """The record names the process that connected, but by the check its pid has another start identity."""
    core.issue(authority.core_socket)
    after_record(monkeypatch, lambda: monkeypatch.setattr(core, "proc_info", lambda pid: core.Proc(1, 0.0, "reused")))
    head = authority.journal.head()
    reply = as_core(authority, append_request("principal", "reused", head), authority.record)
    assert reply["state"] == "refused"
    unchanged(authority, head, "reused")
    assert reasons == [GONE]


def test_a_replaced_launch_record_refuses(authority, reasons):
    core.issue(authority.core_socket)
    head = authority.journal.head()
    fd = os.open(authority.record, os.O_RDONLY)
    try:
        # A new launch replaces the record. Linux names the old file '(deleted)'; it has no link left either way.
        core.issue(authority.core_socket)
        reply = send(authority.path, append_request("principal", "stale", head), [fd])
    finally:
        os.close(fd)
    assert reply["state"] == "refused"
    unchanged(authority, head, "stale")
    assert reasons == [NOT_CURRENT] if sys.platform.startswith("linux") else reasons in ([NOT_CURRENT], [NOT_PRIVATE])


def open_descriptors():
    return len(os.listdir("/dev/fd"))


def test_received_descriptors_are_closed(authority):
    core.issue(authority.core_socket)
    head = authority.journal.head()
    before = open_descriptors()
    for operation, paths in (("two", [authority.record] * 2), ("one", [authority.record])):
        reply = as_core(authority, append_request("principal", operation, head), *paths)
        assert reply["state"] == ("refused" if operation == "two" else "ok")
    assert open_descriptors() == before


def test_record_path_follows_the_core():
    home = Path("/var/lib/exo/exo")
    assert policy.launch_record(home / "principals" / "exo" / "ledger.sqlite") == core.token_path(exod_socket(home))
    # A projection outside a core home has no record, so a core-bound writer cannot append to it.
    for elsewhere in ("/srv/ledger.sqlite", "/principals/exo/ledger.sqlite", "/var/lib/exo/exo/principals/exo/x.sqlite",
                      "/var/lib/exo/exo/stores/exo/ledger.sqlite"):
        assert policy.launch_record(elsewhere) is None


def test_client_refuses_principal_append_without_a_record_before_connecting(tmp_path, monkeypatch):
    identity = {"version": 1, "stream_id": "fixture", "checkpoint_pub": crypto.pubkey_b64(crypto.keygen()),
                "writers": {"principal": {"pub": crypto.pubkey_b64(crypto.keygen()), "author": "principal",
                                          "authors": ["principal"]}}}
    monkeypatch.setattr(client, "verify_socket", lambda *a: pytest.fail("connected without a launch record"))
    intent = {"author": "principal", "kind": "note", "body": {}, "session": None, "refs": []}
    for ledger_path in (None, tmp_path / "home" / "principals" / "exo" / "ledger.sqlite"):
        bound = client.AuthorityClient("fixture", "principal", identity, ledger_path=ledger_path)
        with pytest.raises(AuthorityError, match="launch record") as refused:
            bound.append("op", {"seq": 0, "chain": crypto.GENESIS}, intent)
        assert not isinstance(refused.value, AppendOutcomeUnknown)


@pytest.mark.skipif(sys.platform != "linux", reason="the exo-os client authenticates the signer with Linux credentials")
def test_real_client_from_the_core_appends_and_from_elsewhere_is_refused(authority, monkeypatch):
    monkeypatch.setattr(client, "SOCKET", authority.path)
    monkeypatch.setattr(client, "verify_socket", lambda *a: None)
    monkeypatch.setattr(client, "account_uid", lambda name: os.geteuid())
    monkeypatch.setattr(client, "load_manifest", lambda: authority.manifest)
    core.issue(authority.core_socket)
    bound = client.create_authority(authority.ledger_path, "principal")
    head = bound.head()
    intent = {"author": "principal", "kind": "note", "body": {"said": "yes"}, "session": None, "refs": []}
    record = bound.append("core-op", {"seq": head["seq"], "chain": head["chain"]}, intent)
    assert record["event"]["body"] == {"said": "yes"} and authority.journal.head()["seq"] == head["seq"] + 1
    # The record now names a separate core: this process holds the record but is not that core.
    other = subprocess.Popen([sys.executable, "-c", "import sys, time\nfrom exo import principal\n"
                              "principal.issue(sys.argv[1]); print('ready', flush=True); time.sleep(60)",
                              str(authority.core_socket)], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "ready"
        head = bound.head()
        with pytest.raises(AuthorityError, match="refused") as refused:
            bound.append("elsewhere-op", {"seq": head["seq"], "chain": head["chain"]}, intent)
        assert not isinstance(refused.value, AppendOutcomeUnknown)
        unchanged(authority, head, "elsewhere-op")
    finally:
        other.kill()
        other.wait()

"""The surface's connection against a real exod running the echo engine on a private socket."""
import asyncio
import json
import threading
import time

import pytest

from exo.protocol import schema as s
from exo.server.app import serve
from exo.server.echo import EchoEngine

from exo_os.surface import conn as conn_mod
from exo_os.surface.conn import Connection


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def _add(self, *item):
        with self.lock:
            self.calls.append(item)

    def on_connected(self, up):
        self._add("connected", up)

    def on_session(self, session):
        self._add("session", session)

    def on_approvals(self, rows):
        self._add("approvals", rows)

    def on_event(self, ev):
        self._add("event", ev)

    def on_failed(self, code, message):
        self._add("failed", code)

    def events(self, typ=None):
        with self.lock:
            return [c[1] for c in self.calls if c[0] == "event" and (typ is None or c[1].type == typ)]

    def last(self, kind):
        with self.lock:
            return next((c[1] for c in reversed(self.calls) if c[0] == kind), None)

    def wait(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.02)
        raise AssertionError("timed out")


class Exod:
    """exod with the echo engine in its own thread and loop."""

    def __init__(self, sock):
        self.sock = sock
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.task = asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(5)

    async def _start(self):
        return asyncio.ensure_future(serve(EchoEngine(), socket=self.sock))

    def stop(self):
        self.loop.call_soon_threadsafe(self.task.cancel)
        time.sleep(0.2)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(conn_mod, "BACKOFF_START", 0.05)
    monkeypatch.setattr(conn_mod, "BACKOFF_MAX", 0.1)
    return tmp_path / "exod.sock", tmp_path / "state" / "surface.json"


def _direct(fn, *args):
    fn(*args)


def test_turn_approval_and_state(paths):
    sock, state = paths
    exod = Exod(sock)
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is True and rec.last("session"))
        sid = rec.last("session")
        assert json.loads(state.read_text())["session"] == sid

        c.send_turn("hello there")
        rec.wait(lambda: rec.events("turn.completed"))
        assert "".join(e.body.text for e in rec.events("message.delta")) == "hello there"

        c.send_turn("approve: send it")
        rec.wait(lambda: rec.events("approval.requested"))
        req = rec.events("approval.requested")[0].body
        c.respond_approval(req.request_id, "allow", req.binding)
        rec.wait(lambda: len(rec.events("turn.completed")) == 2)
        assert "approve: send it" in "".join(e.body.text for e in rec.events("message.delta"))

        c.respond_panel("p_missing", "dismissed")  # the echo engine has no ui.respond; panel signals are quiet
        c.send(s.TurnSend(session="s_nobody", text="x"))
        rec.wait(lambda: rec.last("failed") == "not_found")
        assert [c_ for c_ in rec.calls if c_[0] == "failed"] == [("failed", "not_found")]
    finally:
        c.stop()
        exod.stop()
    saved = json.loads(state.read_text())
    assert saved["session"] == sid and saved["seq"] == c.seq > 0


def test_exod_down_then_up_then_resume(paths):
    sock, state = paths
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is False)
        c.send_turn("anyone?")
        assert rec.last("failed") == "not_connected"

        exod = Exod(sock)
        rec.wait(lambda: rec.last("connected") is True and rec.last("session"))
        first = rec.last("session")
        exod.stop()
        rec.wait(lambda: rec.last("connected") is False)

        exod = Exod(sock)  # a new echo engine has forgotten the thread, so a new one is made
        rec.wait(lambda: rec.last("connected") is True and rec.last("session") != first)
        exod.stop()
    finally:
        c.stop()


def test_pending_approvals_are_fetched_on_connect(paths):
    sock, state = paths
    exod = Exod(sock)
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)
    c.start()
    try:
        rec.wait(lambda: rec.last("session"))
        c.send_turn("approve: late client")
        rec.wait(lambda: rec.events("approval.requested"))
    finally:
        c.stop()
    rec2 = Recorder()
    state.unlink()  # a client with no saved seq starts at the head and still sees the open approval
    c2 = Connection(rec2, socket=sock, state=state, post=_direct)
    c2.start()
    try:
        rec2.wait(lambda: rec2.last("approvals"))
        rid = rec2.last("approvals")[0]["request_id"]
        c2.respond_approval(rid, "deny")
        rec2.wait(lambda: rec2.events("turn.completed"))
    finally:
        c2.stop()
        exod.stop()


def test_surface_joins_as_the_principal(tmp_path):
    # Exo D-039: only a principal client may answer approvals; the surface sends the per-launch credential.
    from exo import principal as pr
    from exo_os.surface import conn

    sock = tmp_path / "run" / "exod.sock"
    token = pr.issue(sock)
    client = conn._default_client(str(sock))
    assert client._who() == {pr.HEADER: token, "X-Exo-Client": "surface"}

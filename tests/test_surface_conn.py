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

    def on_approvals(self, rows, through_seq):
        self._add("approvals", rows)

    def on_approval_result(self, request_id):
        self._add("approval_result", request_id)

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
    """exod with the echo engine in its own thread and loop.

    Startup and shutdown wait for actual server readiness/cleanup. A fixed sleep followed by loop.stop
    can strand SSE transports before core's asynchronous close finishes, leaving clients connected.
    """

    def __init__(self, sock):
        self.sock = sock
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.task = asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(5)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    async def _start(self):
        ready = self.loop.create_future()
        task = asyncio.create_task(serve(EchoEngine(), socket=self.sock, ready=lambda _: ready.set_result(None)))
        done, _ = await asyncio.wait((task, ready), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # surface any startup error, rather than waiting for a readiness timeout
        return task

    async def _shutdown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        # Echo turns may still be waiting for an unanswered approval. They belong to this test server.
        remaining = asyncio.all_tasks() - {asyncio.current_task()}
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        await self.loop.shutdown_asyncgens()
        await self.loop.shutdown_default_executor()

    def stop(self):
        if self.loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
        assert not self.thread.is_alive(), "test exod did not shut down"


@pytest.fixture
def paths(tmp_path, socket_dir, monkeypatch):
    monkeypatch.setattr(conn_mod, "BACKOFF_START", 0.05)
    monkeypatch.setattr(conn_mod, "BACKOFF_MAX", 0.1)
    return socket_dir / "exod.sock", tmp_path / "state" / "surface.json"


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
    exod = None
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
        if exod is not None:
            exod.stop()


def test_pending_approvals_are_fetched_on_connect(paths):
    sock, state = paths
    exod = Exod(sock)
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is True and rec.last("session"))
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


def test_stop_twice_does_not_raise(paths):
    sock, state = paths
    exod = Exod(sock)
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is True)
    finally:
        exod.stop()
    c.stop()
    c.stop()  # stopping an already-stopped connection is a no-op, not a crash


class ModelRecorder(Recorder):
    """Exercise the GTK-free listener contract through the real background connection thread."""

    def __init__(self):
        super().__init__()
        from exo_os.surface.model import SurfaceModel
        self.model = SurfaceModel()

    def on_connected(self, up):
        self.model.set_connected(up)
        super().on_connected(up)

    def on_session(self, session):
        self.model.set_session(session)
        super().on_session(session)

    def on_approvals(self, rows, through_seq):
        self.model.reconcile_approvals(rows, through_seq)
        super().on_approvals(rows, through_seq)

    def on_approval_result(self, request_id):
        self.model.approval_result(request_id)
        super().on_approval_result(request_id)

    def on_event(self, ev):
        self.model.apply(ev)
        super().on_event(ev)


class FakeDaemon:
    """Commands and feed can fail independently, including a lost reply after accepting an answer."""

    def __init__(self, *, failure=None):
        self.failure = failure
        self.seq = 1
        self.pending = [{"request_id": "a1", "summary": "Send?", "binding": "h1"}]
        self.clients = []
        self.answers = []
        self.probes = 0
        self.stall_commands = False

    def client(self, socket):
        daemon = self

        class FakeClient:
            closed = False

            async def call(self, body):
                if isinstance(body, s.Hello):
                    daemon.probes += 1
                    if daemon.stall_commands:
                        await asyncio.Event().wait()
                    return {"seq": daemon.seq}
                if isinstance(body, s.SessionCreate):
                    return {"session": {"id": "s_fake"}}
                if isinstance(body, s.ApprovalsList):
                    return {"approvals": list(daemon.pending)}
                raise AssertionError(type(body))

            async def send(self, body):
                if isinstance(body, s.ApprovalRespond):
                    daemon.answers.append(body)
                    if daemon.failure == "lost_applied":
                        daemon.pending = []
                        daemon.seq += 1
                    if daemon.failure in ("lost_applied", "lost_unapplied"):
                        raise ConnectionError("reply lost")
                    if daemon.failure == "rejected":
                        return s.Response(id="x", ok=False, error=s.ErrorInfo(code="stale", message="changed"))
                    daemon.pending = []
                    daemon.seq += 1
                return s.Response(id="x", ok=True, result={})

            async def events(self, **kwargs):
                await asyncio.Event().wait()
                yield  # stalled event socket, healthy or stalled commands are independent

            async def aclose(self):
                self.closed = True

        client = FakeClient()
        self.clients.append(client)
        return client


@pytest.mark.parametrize("failure", ["lost_unapplied", "lost_applied", "rejected", None])
def test_answer_delivery_is_reconciled_without_automatic_resend(paths, failure):
    sock, state = paths
    sock.touch()
    daemon = FakeDaemon(failure=failure)
    rec = ModelRecorder()
    c = Connection(rec, socket=sock, state=state, post=_direct, client_factory=daemon.client)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is True and rec.model.approval is not None)
        body = rec.model.answer("allow")
        assert rec.model.answer("allow") is None
        c.send(body)
        rec.wait(lambda: not rec.model.answering)
        assert len(daemon.answers) == 1
        if failure in ("lost_unapplied", "rejected"):
            assert rec.model.approval.request_id == "a1"
            assert rec.model.approval.binding == "h1"
        else:
            assert rec.model.approval is None
        if failure and failure.startswith("lost"):
            assert len(daemon.clients) >= 2
            assert daemon.clients[0].closed
    finally:
        c.stop()


def test_healthy_idle_event_stream_stays_connected(paths, monkeypatch):
    sock, state = paths
    sock.touch()
    monkeypatch.setattr(conn_mod, "PROBE_EVERY", 0.01)
    daemon = FakeDaemon()
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct, client_factory=daemon.client)
    c.start()
    try:
        rec.wait(lambda: daemon.probes >= 5)
        assert rec.last("connected") is True
        assert len(daemon.clients) == 1
    finally:
        c.stop()


@pytest.mark.parametrize("stalled", ["commands", "events"])
def test_stalled_connection_rejoins_with_bounded_probes(paths, monkeypatch, stalled):
    sock, state = paths
    sock.touch()
    monkeypatch.setattr(conn_mod, "PROBE_EVERY", 0.01)
    monkeypatch.setattr(conn_mod, "COMMAND_TIMEOUT", 0.04)
    monkeypatch.setattr(conn_mod, "EVENT_LAG_TIMEOUT", 0.025)
    daemon = FakeDaemon()
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct, client_factory=daemon.client)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is True)
        if stalled == "commands":
            daemon.stall_commands = True
        else:
            daemon.seq += 1  # a quiet stream is only proven stalled when hello reports undelivered events
        rec.wait(lambda: len(daemon.clients) >= 2)
        assert daemon.clients[0].closed
        assert ("connected", False) in rec.calls
    finally:
        c.stop()


def test_reconnect_exceptions_are_logged_with_traceback_and_rate_limited(paths, monkeypatch, caplog):
    sock, state = paths
    sock.touch()
    daemon = FakeDaemon()
    c = Connection(Recorder(), socket=sock, state=state, post=_direct, client_factory=daemon.client)
    now = [100.0]
    monkeypatch.setattr(conn_mod.time, "monotonic", lambda: now[0])
    c._log_failure(KeyError("session"))
    for i in range(20):
        c._log_failure(ValueError(f"bad reply {i}"))  # changing errors must not flood the journal either
    assert len(caplog.records) == 1 and caplog.records[0].exc_info
    now[0] += conn_mod.LOG_EVERY
    c._log_failure(ValueError("latest reply"))
    assert len(caplog.records) == 2
    assert "suppressed 20 retries" in caplog.records[-1].message
    assert "latest reply" in caplog.records[-1].message


def test_snapshot_posts_before_resolution_received_during_query(paths):
    sock, state = paths
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)

    async def exercise():
        queried = asyncio.Event()
        release = asyncio.Event()

        class Client:
            async def call(self, body):
                queried.set()
                await release.wait()
                return {"approvals": [{"request_id": "a1"}]}

            async def events(self, **kwargs):
                yield s.Event.of(s.ApprovalResolved(request_id="a1", decision="allow"), 2, "s1")

        client = Client()
        snapshot = asyncio.create_task(c._snapshot(client, 1))
        await queried.wait()
        feed = asyncio.create_task(c._events(client, 1))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(snapshot, feed)

    asyncio.run(exercise())
    assert [call[0] for call in rec.calls] == ["approvals", "event"]


def test_malformed_reconnect_reply_is_observable_and_retried(paths, caplog):
    sock, state = paths
    sock.touch()
    attempts = []

    class BadClient:
        async def call(self, body):
            return {"seq": 0} if isinstance(body, s.Hello) else {}  # SessionCreate is malformed

        async def aclose(self):
            pass

    def factory(socket):
        attempts.append(socket)
        return BadClient()

    c = Connection(Recorder(), socket=sock, state=state, post=_direct, client_factory=factory)
    c.start()
    try:
        c.listener.wait(lambda: len(attempts) >= 3)
        assert len(caplog.records) == 1
        assert "KeyError" in caplog.records[0].message and caplog.records[0].exc_info
        assert c.listener.last("connected") is False
    finally:
        c.stop()


def test_events_arriving_during_probe_are_not_a_daemon_reset(paths, monkeypatch):
    sock, state = paths
    monkeypatch.setattr(conn_mod, "PROBE_EVERY", 0.001)
    c = Connection(Recorder(), socket=sock, state=state, post=_direct)
    c.seq = 5
    probes = []

    class Client:
        async def call(self, body):
            before = c.seq
            c.seq += 1  # event delivery overtakes an older hello response
            probes.append(before)
            return {"seq": before}

    async def exercise():
        task = asyncio.create_task(c._probe(Client()))
        for _ in range(100):
            await asyncio.sleep(0.001)
            if len(probes) >= 3:
                break
        assert len(probes) >= 3 and not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("applied", [False, True])
def test_reconnect_cancels_inflight_answer_before_rearming(paths, applied):
    sock, state = paths
    sock.touch()
    daemon = FakeDaemon()
    entered = threading.Event()
    cancelled = threading.Event()

    def factory(socket):
        client = daemon.client(socket)
        original_send = client.send

        async def send(body):
            if not isinstance(body, s.ApprovalRespond):
                return await original_send(body)
            daemon.answers.append(body)
            if applied:
                daemon.pending = []
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        client.send = send
        return client

    rec = ModelRecorder()
    c = Connection(rec, socket=sock, state=state, post=_direct, client_factory=factory)
    c.start()
    try:
        rec.wait(lambda: rec.model.approval is not None and rec.model.connected)
        c.send(rec.model.answer("allow"))
        assert entered.wait(2)
        c._loop.call_soon_threadsafe(c._reconnect.set)
        rec.wait(lambda: len(daemon.clients) >= 2 and not rec.model.answering)
        assert cancelled.is_set() and len(daemon.answers) == 1
        assert (rec.model.approval is None) is applied
    finally:
        c.stop()


@pytest.mark.parametrize("stable_third_connection", [False, True])
def test_closing_event_feeds_back_off_until_a_stable_connection(paths, monkeypatch, stable_third_connection):
    sock, state = paths
    sock.touch()
    daemon = FakeDaemon()
    clock = [100.0]
    delays = []
    monkeypatch.setattr(conn_mod, "BACKOFF_START", 0.5)
    monkeypatch.setattr(conn_mod, "BACKOFF_MAX", 2.0)
    monkeypatch.setattr(conn_mod.time, "monotonic", lambda: clock[0])

    def factory(socket):
        client = daemon.client(socket)
        number = len(daemon.clients)

        async def events(**kwargs):
            if stable_third_connection and number == 3:
                clock[0] += conn_mod.BACKOFF_RESET_AFTER
            if False:
                yield  # a successful handshake followed by an immediately closed SSE feed

        client.events = events
        return client

    c = Connection(Recorder(), socket=sock, state=state, post=_direct, client_factory=factory)

    async def no_probe(client):
        await asyncio.Event().wait()

    async def capture_sleep(delay):
        delays.append(delay)
        if len(delays) == 6:
            raise asyncio.CancelledError
        # No actual wait: _follow still exercises real event task completion and cleanup each time.

    monkeypatch.setattr(c, "_probe", no_probe)
    monkeypatch.setattr(conn_mod.asyncio, "sleep", capture_sleep)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await c._run()

    asyncio.run(exercise())
    expected = [0.5, 1.0, 0.5, 1.0, 2.0, 2.0] if stable_third_connection else [0.5, 1.0, 2.0, 2.0, 2.0, 2.0]
    assert delays == expected
    assert len(daemon.clients) == 6 and all(client.closed for client in daemon.clients)


def test_exod_fixture_shutdown_drains_open_streams_and_unanswered_turn(paths):
    sock, state = paths
    exod = Exod(sock)
    rec = Recorder()
    c = Connection(rec, socket=sock, state=state, post=_direct)
    c.start()
    try:
        rec.wait(lambda: rec.last("connected") is True)
        c.send_turn("approve: still unanswered on shutdown")
        rec.wait(lambda: rec.events("approval.requested"))
        exod.stop()
        assert exod.loop.is_closed() and not exod.thread.is_alive()
        assert not sock.exists()
        assert not asyncio.all_tasks(exod.loop)
        rec.wait(lambda: rec.last("connected") is False)
        exod.stop()  # cleanup remains safe if a failing test's finally block calls it again
    finally:
        c.stop()
        exod.stop()

"""The surface's link to exod: one background thread running asyncio, so GTK's main loop never waits on the
network.

The surface keeps one Exo thread (a protocol session) for the desktop and remembers it, with the last event
seq it saw, in $XDG_STATE_HOME/exo-os/surface.json. After a restart or a dropped link it resumes that thread
and replays the events it missed, so an answer or an approval that arrived meanwhile is not lost.

It follows events of every thread, not only its own: an approval from a scheduled job waits for the person
too. The model decides what to show. If exod is down the link retries with backoff and says so once; the
surface still opens apps (DESIGN.md §7.3, "a way out").

Callbacks reach the listener through `post`, which is GLib.idle_add in the app, so the listener only ever
runs on the GTK thread.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from exo.protocol import schema as s

from .. import paths

CLIENT = "exo-surface"
CAPABILITIES = ["markdown", "diff"]
THREAD_TITLE = "Desktop"
BACKOFF_START, BACKOFF_MAX = 0.5, 10.0
BACKOFF_RESET_AFTER = 30.0  # brief handshakes or immediately closing feeds do not reset retries
# Resume a replay only if it is shorter than this; a longer gap starts from now (pending approvals are
# still fetched with approvals.list, so nothing that needs an answer is skipped).
REPLAY_MAX = 2000
SAVE_EVERY = 2.0
COMMAND_TIMEOUT = 10.0
PROBE_EVERY = 15.0
EVENT_LAG_TIMEOUT = 15.0
LOG_EVERY = 60.0
LOG = logging.getLogger(__name__)


def socket_path() -> Path:
    """Same place `exo serve` listens: $EXO_HOME/run/exod.sock."""
    return paths.exo_home() / "run" / "exod.sock"


def state_path() -> Path:
    return paths.state_home() / "surface.json"


class Listener(Protocol):
    """What the connection tells the app. Every call arrives on the GTK thread."""

    def on_connected(self, up: bool) -> None: ...
    def on_session(self, session: str) -> None: ...
    def on_approvals(self, rows: list[dict[str, Any]], through_seq: int) -> None: ...
    def on_approval_result(self, request_id: str) -> None: ...
    def on_event(self, ev: s.Event) -> None: ...
    def on_failed(self, code: str, message: str) -> None: ...


def _glib_post(fn: Callable[..., Any], *args: Any) -> None:
    from gi.repository import GLib

    def once() -> bool:
        fn(*args)
        return False  # GLib.SOURCE_REMOVE: run once

    GLib.idle_add(once)


class Connection:
    def __init__(self, listener: Listener, *, socket: Path | None = None, state: Path | None = None,
                 post: Callable[..., None] | None = None,
                 client_factory: Callable[[str], Any] | None = None) -> None:
        self.listener = listener
        self.socket = socket or socket_path()
        self.state_file = state or state_path()
        self.post = post or _glib_post
        self._client_factory = client_factory or _default_client
        self.state = self._load()
        self.session: str | None = None
        self.seq = 0
        self.up: bool | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._synced = False  # the pending approvals are reconciled; only then may an answer go out
        self._connected_at = 0.0
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._saved_at = 0.0
        self._commands: set[asyncio.Task] = set()
        self._snapshot_lock = asyncio.Lock()
        self._reconnect = asyncio.Event()
        self._last_failure: tuple[type, str] | None = None
        self._logged_at = 0.0
        self._suppressed = 0

    # -- life

    def start(self) -> None:
        ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, args=(ready,), name="exo-surface-conn",
                                        daemon=True)
        self._thread.start()
        ready.wait(5)

    def stop(self) -> None:
        loop, task = self._loop, self._task
        self._loop, self._task, thread = None, None, self._thread
        self._thread = None
        if loop is not None and task is not None and not loop.is_closed():
            loop.call_soon_threadsafe(task.cancel)
        if thread is not None:
            thread.join(5)
        self._save(force=True)

    def _thread_main(self, ready: threading.Event) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._task = loop.create_task(self._run())
        ready.set()
        try:
            loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            loop.close()

    # -- commands, callable from any thread

    def send(self, body: Any, quiet: bool = False) -> None:
        """Send one command; a refusal or a dead link reaches the listener as on_failed(code, message).
        quiet drops failures, for signals such as ui.respond that the person never asked for."""
        loop = self._loop
        # A turn may go out as soon as the session is known (`_client` is set before the approval snapshot).
        # An approval answer waits for the snapshot: sent earlier, a stale row could reopen its card.
        if loop is None or self._client is None or (isinstance(body, s.ApprovalRespond) and not self._synced):
            if isinstance(body, s.ApprovalRespond):
                self.post(self.listener.on_approval_result, body.request_id)
            if not quiet:
                self.post(self.listener.on_failed, "not_connected", "exod is not reachable")
            return
        client = self._client
        def schedule() -> None:
            task = asyncio.create_task(self._send(body, quiet, client=client))
            self._commands.add(task)
            task.add_done_callback(self._commands.discard)
        loop.call_soon_threadsafe(schedule)

    def send_turn(self, text: str, mode: str = "send") -> None:
        if self.session is None:
            self.post(self.listener.on_failed, "not_connected", "no thread yet")
            return
        self.send(s.TurnSend(session=self.session, text=text, mode=mode))  # type: ignore[arg-type]

    def respond_approval(self, request_id: str, decision: str, binding: str = "") -> None:
        self.send(s.ApprovalRespond(request_id=request_id, decision=decision,  # type: ignore[arg-type]
                                    client=CLIENT, binding=binding))

    def respond_panel(self, panel_id: str, action: str, value: dict[str, Any] | None = None) -> None:
        self.send(s.UiRespond(panel_id=panel_id, action=action, value=value or {}),  # type: ignore[arg-type]
                  quiet=True)

    async def _send(self, body: Any, quiet: bool = False, *, client: Any = None) -> None:
        approval = isinstance(body, s.ApprovalRespond)
        try:
            if client is None or client is not self._client:
                raise ConnectionError("exod is not reachable")
            resp = await asyncio.wait_for(client.send(body), COMMAND_TIMEOUT)
        except asyncio.CancelledError:
            if approval:
                self.post(self.listener.on_approval_result, body.request_id)
            raise
        except Exception as exc:
            if approval:
                self.post(self.listener.on_approval_result, body.request_id)
            if not quiet:
                self.post(self.listener.on_failed, "not_connected", f"{type(exc).__name__}: {exc}")
            # The reply may be lost after exod acted. Rejoin and query pending state; never resend.
            if client is self._client:
                self._reconnect.set()
            return
        if approval:
            self.post(self.listener.on_approval_result, body.request_id)
        if not resp.ok and not quiet:
            err = resp.error
            self.post(self.listener.on_failed, err.code if err else "unknown", err.message if err else "")
        if approval:
            try:
                await self._snapshot(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_failure(exc)
                self._reconnect.set()

    # -- the follow loop

    def _log_failure(self, exc: Exception) -> None:
        """Log the first error and the latest repeated failure once a minute, with a retry count."""
        key = (type(exc), str(exc))
        now = time.monotonic()
        if self._last_failure is None or now - self._logged_at >= LOG_EVERY:
            LOG.warning("exod connection failed: %s: %s (suppressed %d retries)",
                        type(exc).__name__, exc, self._suppressed, exc_info=True)
            self._last_failure, self._logged_at, self._suppressed = key, now, 0
        else:
            self._suppressed += 1

    async def _run(self) -> None:
        delay = BACKOFF_START
        while True:
            client = None
            try:
                if self.socket.exists():
                    client = self._client_factory(str(self.socket))
                    await self._follow(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_failure(exc)
            finally:
                was_stable = (self._client is not None
                              and time.monotonic() - self._connected_at >= BACKOFF_RESET_AFTER)
                self._client = None
                self._synced = False
                self._set_up(False)
                # An old answer must finish/cancel before the new pending snapshot rearms its card.
                commands = list(self._commands)
                for task in commands:
                    task.cancel()
                if commands:
                    await asyncio.gather(*commands, return_exceptions=True)
                if client is not None:
                    try:
                        await asyncio.wait_for(client.aclose(), COMMAND_TIMEOUT)
                    except Exception as exc:
                        self._log_failure(exc)
            self._save(force=True)
            if was_stable:
                delay = BACKOFF_START
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX)

    async def _call(self, client: Any, body: Any) -> dict[str, Any]:
        return await asyncio.wait_for(client.call(body), COMMAND_TIMEOUT)

    async def _snapshot(self, client: Any, head: int | None = None) -> None:
        # Hold event delivery until the snapshot is posted. A resolution received during the query must
        # follow its rows on GTK's queue, otherwise a stale row could reopen an already-resolved card.
        async with self._snapshot_lock:
            if head is None:
                head = int((await self._call(client, s.Hello(client=CLIENT)))["seq"])
            rows = (await self._call(client, s.ApprovalsList()))["approvals"]
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("request_id"), str)
                or not row["request_id"] for row in rows
            ):
                raise ValueError("invalid approvals.list reply")
            self.post(self.listener.on_approvals, rows, head)  # empty is authoritative too

    async def _follow(self, client: Any) -> None:
        hello = await self._call(client, s.Hello(client=CLIENT, capabilities=CAPABILITIES))
        head = int(hello["seq"])
        await self._ensure_session(client)
        since = self._since(head)
        self.seq = since  # an idle stream is caught up at its subscription cursor
        self._reconnect.clear()
        # Take commands as soon as the session is known: the UI shows it at once, and the snapshot below
        # takes two round trips. Turns need no snapshot (the feed replays from `since`); answers still do.
        self._client = client
        self._connected_at = time.monotonic()
        await self._snapshot(client, head)
        self._synced = True
        self._set_up(True)
        feed = asyncio.create_task(self._events(client, since))
        probe = asyncio.create_task(self._probe(client))
        reconnect = asyncio.create_task(self._reconnect.wait())
        try:
            done, _ = await asyncio.wait((feed, probe, reconnect), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise ConnectionError("event connection ended or needs reconciliation")
        finally:
            for task in (feed, probe, reconnect):
                task.cancel()
            await asyncio.gather(feed, probe, reconnect, return_exceptions=True)

    async def _events(self, client: Any, since: int) -> None:
        async for ev in client.events(since=since, reconnect=False):
            async with self._snapshot_lock:
                self.seq = ev.seq
                self.post(self.listener.on_event, ev)
                self._save()

    async def _probe(self, client: Any) -> None:
        behind_at: float | None = None
        target = 0
        while True:
            await asyncio.sleep(PROBE_EVERY)
            before_probe = self.seq
            head = int((await self._call(client, s.Hello(client=CLIENT)))["seq"])
            # Events can arrive while the hello reply is in flight. Its head may legitimately be
            # lower than the cursor now, but cannot be lower than the cursor before the probe.
            if head < before_probe:
                raise ConnectionError("exod event sequence reset")
            if behind_at is not None and self.seq >= target:
                behind_at = None
            if behind_at is None and head > self.seq:
                behind_at, target = time.monotonic(), head
            if behind_at is not None and time.monotonic() - behind_at >= EVENT_LAG_TIMEOUT:
                raise TimeoutError("event stream did not catch up to exod")

    async def _ensure_session(self, client: Any) -> None:
        saved = self.state.get("session")
        if saved:
            resp = await asyncio.wait_for(client.send(s.SessionResume(session=saved)), COMMAND_TIMEOUT)
            if resp.ok:
                self._use_session(saved)
                return
        got = await self._call(client, s.SessionCreate(title=THREAD_TITLE))
        self._use_session(got["session"]["id"])

    def _use_session(self, session: str) -> None:
        if session != self.state.get("session"):
            self.state = {"session": session, "seq": self.seq or self.state.get("seq", 0)}
            self._save(force=True)
        self.session = session
        self.post(self.listener.on_session, session)

    def _since(self, head: int) -> int:
        """Where to resume: the last seq seen (this run, else the saved one) if the gap is small."""
        last = self.seq or int(self.state.get("seq") or 0)
        if 0 < last <= head and head - last <= REPLAY_MAX:
            return last
        return head

    def _set_up(self, up: bool) -> None:
        if up != self.up:
            self.up = up
            self.post(self.listener.on_connected, up)

    # -- state file

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._saved_at < SAVE_EVERY:
            return
        self._saved_at = now
        data = {"session": self.session or self.state.get("session"), "seq": self.seq or self.state.get("seq", 0)}
        if not data["session"]:
            return
        self.state = data
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self.state_file)
        except OSError:
            pass  # a read-only home must not take the surface down


def _default_client(socket: str) -> Any:
    from exo.protocol.client import Client

    # The surface is the person's own window, so it joins as a principal client (Exo D-039): it sends the core's
    # per-launch credential, and only such a client may answer an approval card.
    return Client(socket=socket, timeout=10.0, principal="surface")

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
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from exo.protocol import schema as s

CLIENT = "exo-surface"
CAPABILITIES = ["markdown", "diff"]
THREAD_TITLE = "Desktop"
BACKOFF_START, BACKOFF_MAX = 0.5, 10.0
# Resume a replay only if it is shorter than this; a longer gap starts from now (pending approvals are
# still fetched with approvals.list, so nothing that needs an answer is skipped).
REPLAY_MAX = 2000
SAVE_EVERY = 2.0


def socket_path() -> Path:
    """Same default as `exo serve`: $EXO_HOME/run/exod.sock, EXO_HOME defaulting to ~/.exo."""
    return Path(os.environ.get("EXO_HOME") or Path.home() / ".exo").expanduser() / "run" / "exod.sock"


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base).expanduser() / "exo-os" / "surface.json"


class Listener(Protocol):
    """What the connection tells the app. Every call arrives on the GTK thread."""

    def on_connected(self, up: bool) -> None: ...
    def on_session(self, session: str) -> None: ...
    def on_approvals(self, rows: list[dict[str, Any]]) -> None: ...
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
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._saved_at = 0.0

    # -- life

    def start(self) -> None:
        ready = threading.Event()
        self._thread = threading.Thread(target=self._thread_main, args=(ready,), name="exo-surface-conn",
                                        daemon=True)
        self._thread.start()
        ready.wait(5)

    def stop(self) -> None:
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        if self._thread is not None:
            self._thread.join(5)
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

    def send(self, body: Any) -> None:
        """Send one command; a refusal or a dead link reaches the listener as on_failed(code, message)."""
        loop = self._loop
        if loop is None or self._client is None:
            self.post(self.listener.on_failed, "not_connected", "exod is not reachable")
            return
        asyncio.run_coroutine_threadsafe(self._send(body), loop)

    def send_turn(self, text: str, mode: str = "send") -> None:
        if self.session is None:
            self.post(self.listener.on_failed, "not_connected", "no thread yet")
            return
        self.send(s.TurnSend(session=self.session, text=text, mode=mode))  # type: ignore[arg-type]

    def respond_approval(self, request_id: str, decision: str, binding: str = "") -> None:
        self.send(s.ApprovalRespond(request_id=request_id, decision=decision,  # type: ignore[arg-type]
                                    client=CLIENT, binding=binding))

    def respond_panel(self, panel_id: str, action: str, value: dict[str, Any] | None = None) -> None:
        self.send(s.UiRespond(panel_id=panel_id, action=action, value=value or {}))  # type: ignore[arg-type]

    async def _send(self, body: Any) -> None:
        client = self._client
        if client is None:
            self.post(self.listener.on_failed, "not_connected", "exod is not reachable")
            return
        try:
            resp = await client.send(body)
        except Exception as exc:  # the link dropped mid-send; the follower will notice and reconnect
            self.post(self.listener.on_failed, "not_connected", f"{type(exc).__name__}: {exc}")
            return
        if not resp.ok:
            err = resp.error
            self.post(self.listener.on_failed, err.code if err else "unknown", err.message if err else "")

    # -- the follow loop

    async def _run(self) -> None:
        delay = BACKOFF_START
        while True:
            if self.socket.exists():
                client = self._client_factory(str(self.socket))
                try:
                    await self._follow(client)
                    delay = BACKOFF_START  # it was up; a drop retries quickly
                except asyncio.CancelledError:
                    raise
                except Exception:  # refused, reset, bad reply: all mean "not reachable now"
                    pass
                finally:
                    self._client = None
                    await client.aclose()
            self._set_up(False)
            self._save(force=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX)

    async def _follow(self, client: Any) -> None:
        hello = await client.call(s.Hello(client=CLIENT, capabilities=CAPABILITIES))
        head = int(hello.get("seq", 0))
        await self._ensure_session(client)
        since = self._since(head)
        self._client = client
        self._set_up(True)
        rows = (await client.call(s.ApprovalsList())).get("approvals", [])
        if rows:
            self.post(self.listener.on_approvals, list(rows))
        async for ev in client.events(since=since, reconnect=False):
            self.seq = ev.seq
            self.post(self.listener.on_event, ev)
            self._save()

    async def _ensure_session(self, client: Any) -> None:
        saved = self.state.get("session")
        if saved:
            resp = await client.send(s.SessionResume(session=saved))
            if resp.ok:
                self._use_session(saved)
                return
        got = await client.call(s.SessionCreate(title=THREAD_TITLE))
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

    return Client(socket=socket, timeout=10.0)

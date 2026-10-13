"""The GTK side. Pure helpers run everywhere. The widget smoke test runs in a child process on a private
broadway display (gtk4-broadwayd, an in-memory GTK display server), never on the desktop this runs on.
Set EXO_SURFACE_TEST_DISPLAY=1 to run it on the inherited display instead (for the Exo OS VM)."""
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytest.importorskip("gi")
from exo_os.surface import app  # noqa: E402  (loads Gio only)

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_needs_preload():
    lib = "libgtk4-layer-shell.so.0"
    wl = {"WAYLAND_DISPLAY": "wayland-1"}
    assert app.needs_preload(wl, lib)
    assert not app.needs_preload(wl, None)  # library not installed: plain windows, no re-exec
    assert not app.needs_preload({**wl, "LD_PRELOAD": "/usr/lib/libgtk4-layer-shell.so"}, lib)
    assert not app.needs_preload({**wl, app.PRELOAD_MARK: "1"}, lib)
    assert not app.needs_preload({**wl, "GDK_BACKEND": "broadway"}, lib)
    assert not app.needs_preload({"DISPLAY": ":0"}, lib)
    env = app.preload_env({"LD_PRELOAD": "libfoo.so"}, "libgtk4-layer-shell.so.0")
    assert env["LD_PRELOAD"] == "libgtk4-layer-shell.so.0:libfoo.so" and env[app.PRELOAD_MARK] == "1"


def test_toggle_path_does_not_load_gtk():
    code = "import sys, exo_os.surface.app; print('gi.repository.Gtk' in sys.modules, 'exo_os.surface.ui' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": SRC}, capture_output=True,
                         text=True, timeout=60)
    assert out.stdout.split() == ["False", "False"], out.stderr


def test_load_profile_with_wrong_shaped_toml_uses_default(tmp_path):
    from exo_os.profile import Profile

    (tmp_path / "machine.toml").write_text("apps = 5\n")  # valid TOML, wrong shape: not a ValueError
    assert app.load_profile(tmp_path / "machine.toml").data == Profile.default().data


def test_unknown_command_is_refused():
    assert app.main(["exo-surface", "dance"]) == 2


def test_load_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_OS_MACHINE", str(tmp_path))
    assert app.load_profile().get("surface.plain") is True  # no file yet: the default
    (tmp_path / "machine.toml").write_text('[surface]\nlanguage = "ur"\n')
    assert app.load_profile().get("surface.language") == "ur"
    # a broken profile falls back to the default instead of keeping the bar shut
    (tmp_path / "machine.toml").write_text("[nonsense]\n")
    assert app.load_profile().get("surface.language") == "en"


CHILD = textwrap.dedent('''
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    assert Gtk.init_check(), "no display"

    from exo.core.types import Message
    from exo.protocol import schema as s
    from exo_os.profile import Profile
    from exo_os.surface import ui as A
    from exo_os.surface.model import SurfaceModel

    def pump():
        ctx = GLib.MainContext.default()
        for _ in range(50):
            while ctx.iteration(False):
                pass

    SID = "s_1"
    seq = iter(range(1, 1000))
    def ev(body):
        return s.Event.of(body, next(seq), SID)

    prof = Profile.default().put_app({"name": "WhatsApp", "url": "https://web.whatsapp.com/"})
    model = SurfaceModel(prof)
    model.set_session(SID)
    sent, launched = [], []
    ui = A.SurfaceUI(model, sent.append, launch=lambda name, fail: launched.append(name))
    ui.on_connected(True)
    ui.show()
    pump()
    assert ui.bar.get_visible()

    ui.bar.entry.set_text("whats")
    pump()
    row = ui.bar.suggestions.get_row_at_index(0)
    assert row.get_child().get_text() == "Open WhatsApp"
    ui.bar.entry.emit("activate")
    pump()
    assert launched == ["WhatsApp"] and not ui.bar.get_visible()

    ui.show()
    ui.bar.entry.set_text("who do I call today?")
    ui.bar.entry.emit("activate")
    assert isinstance(sent[-1], s.TurnSend) and sent[-1].text == "who do I call today?"
    ui.on_event(ev(s.TurnStarted(turn_id="t1", model="m:x")))
    ui.on_event(ev(s.MessageDelta(message_id="m1", text="Call Lead A.")))
    pump()
    texts = []
    child = ui.bar.lines.get_first_child()
    while child is not None:
        texts.append(child.get_text())
        child = child.get_next_sibling()
    assert texts == ["who do I call today?", "Call Lead A."], texts
    assert ui.bar.status.get_text() == "Working…"

    for kind, data in [("table", {"columns": ["Who"], "rows": [["Lead A"]]}),
                       ("tasks", {"tasks": [{"title": "Call", "status": "done"}]}),
                       ("choice", {"prompt": "Which?", "options": [{"id": "a", "label": "Short"}]}),
                       ("chart", {"labels": ["Mon"], "values": [3]}),
                       ("diff", {"files": [{"path": "a.txt", "diff": "-a\\n+b"}]}),
                       ("file", {"path": "f.txt", "content": "hello"}),
                       ("markdown", {"markdown": "# Today\\n- one"}),
                       ("approval", {"request_id": "a0", "summary": "Send?"})]:
        ui.on_event(ev(s.UiPanel(panel_id="p_" + kind, action="open", kind=kind, title=kind, text="text " + kind,
                                 data=data)))
    pump()
    assert ui.drawer.get_visible()
    opened = [b.panel_id for b in sent if isinstance(b, s.UiRespond) and b.action == "opened"]
    assert len(opened) == 8, opened
    ui.choose("p_choice", "a")
    assert sent[-2] == s.UiRespond(panel_id="p_choice", action="acted", value={"choice": "a"})
    ui.close_panel("p_table")
    assert sent[-1] == s.UiRespond(panel_id="p_table", action="dismissed")

    msg = Message.assistant("Call Lead A.")
    msg.id = "m1"
    ui.on_event(ev(s.MessageCompleted(message=msg)))
    ui.on_event(ev(s.TurnCompleted(turn_id="t1")))
    n = len(sent)
    ui.bar.entry.set_text("budget")
    ui.bar.entry.emit("activate")
    pump()
    assert len(sent) == n + 1 and isinstance(sent[-1], s.ModelsList)
    ui.on_model_spend({"state": "stopped", "daily_cap_usd": "1.000000000",
        "monthly_cap_usd": "5.000000000", "day_charged_usd": "0.000000001",
        "month_charged_usd": "0.000000001", "pending_reserved_usd": "0.500000000",
        "pending_count": 1})
    pump()
    child, texts = ui.bar.lines.get_first_child(), []
    while child is not None:
        texts.append(child.get_text())
        child = child.get_next_sibling()
    shown = "\\n".join(texts)
    assert "Daily limit: $1.00. Monthly limit: $5.00." in shown, shown
    assert "$0.000000001 today" in shown and "Unsettled calls: 1" in shown, shown
    assert "administrator" in shown and not ui.card.get_visible()
    assert len(sent) == n + 1  # inspection creates no turn or consent response
    ui.on_event(ev(s.ApprovalRequested(request_id="a1", summary="Send this message to Ahmed?", binding="h1")))
    pump()
    assert ui.card.get_visible() and ui.card.summary.get_text() == "Send this message to Ahmed?"
    n = len(sent)
    ui.card._answer("allow")  # too soon after it appeared: ignored
    assert len(sent) == n and ui.card.get_visible()
    ui.card.shown_at -= (A.ARM_MS + 50) * 1000
    ui.card._key(None, A.Gdk.KEY_y, 0, None)
    pump()
    assert sent[-1] == s.ApprovalRespond(request_id="a1", decision="allow", client="exo-surface", binding="h1")
    assert not ui.card.get_visible()

    ui.on_event(ev(s.ApprovalRequested(request_id="a2", summary="Delete everything?", binding="h2")))
    pump()
    assert ui.card.get_visible() and ui.card.summary.get_text() == "Delete everything?"
    n = len(sent)
    ui.card._answer("allow")  # a key still held from the last answer: the new card is not armed yet
    assert len(sent) == n and ui.card.get_visible()

    ui.card._key(None, A.Gdk.KEY_Escape, 0, None)  # dismissal works even before arming
    pump()
    assert len(sent) == n and not ui.card.get_visible()
    assert model.approvals[-1].request_id == "a2" and not model.answer_check
    ui.show()
    pump()
    assert ui.card.get_visible() and ui.card.summary.get_text() == "Delete everything?"
    ui.card._answer("deny")
    assert len(sent) == n  # reopening also gets the anti-misclick arm window
    ui.on_event(ev(s.ApprovalResolved(request_id="a2", decision="deny")))
    from exo_os.egress import policy
    import json
    origin = "https://api.example.test"
    policy.load_policy = lambda: {"messages": policy.Profile(origin, ("POST",), "/messages", "hidden_token")}
    payload = "<b>literal markup</b>" + "full body " * 1200 + "PAYLOAD_SUFFIX"
    call = s.ToolCall(id="c_outbound", name="credential_request", arguments={
        "profile": "messages", "request_id": "operation_123", "expected_origin": origin,
        "method": "POST", "path": "/messages/send", "body": payload})
    ui.on_event(ev(s.ApprovalRequested(request_id="a3", summary="Misleading recipient", call=call,
                                     binding="bound_origin_and_payload")))
    pump()
    assert ui.card.detail_scroll.get_visible() and isinstance(ui.card.detail, Gtk.TextView)
    assert not ui.card.detail.get_editable() and ui.card.detail.get_cursor_visible()
    buf = ui.card.detail.get_buffer()
    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert json.loads(text.split("\\n", 1)[1])["request"]["body"] == payload
    assert "PAYLOAD_SUFFIX" in text and origin in ui.card.summary.get_text()
    assert ui.card.detail_scroll.get_max_content_height() == 360
    buf.select_range(buf.get_start_iter(), buf.get_end_iter())
    assert buf.get_has_selection()  # full request can be selected and copied
    assert ui.card.options == ["allow", "deny"]
    ui.hide()
    pump()
    assert not ui.bar.get_visible() and not ui.drawer.get_visible()
    print("SMOKE OK", "layer-shell" if ui.bar.layered else "plain windows")
''')


def _broadway(tmp_path):
    exe = shutil.which("gtk4-broadwayd")
    if not exe:
        return None, None
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    display = f":{40 + os.getpid() % 50}"
    proc = subprocess.Popen([exe, display, "--unixsocket", str(run / "http.sock")],
                            env={**os.environ, "XDG_RUNTIME_DIR": str(run)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY", "LD_PRELOAD")}
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=display, XDG_RUNTIME_DIR=str(run))
    return proc, env


def test_widgets_smoke(tmp_path):
    if os.environ.get("EXO_SURFACE_TEST_DISPLAY"):
        proc, env = None, dict(os.environ)
    else:
        proc, env = _broadway(tmp_path)
        if proc is None:
            pytest.skip("no private display: install gtk4-broadwayd or set EXO_SURFACE_TEST_DISPLAY=1")
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    try:
        out = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=60)
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(5)
    assert "SMOKE OK" in out.stdout, out.stdout + out.stderr

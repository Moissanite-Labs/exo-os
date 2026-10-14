#!/usr/bin/env python3
"""Synthetic GTK surface checks on private Broadway or an authenticated disposable Wayland VM.

This creates widgets with fake protocol events only. It does not connect to exod,
send a model request, launch an app, or read a credential.
"""
import argparse
import json
import os
import pwd
import subprocess
import sys
import time
from pathlib import Path

from phase3_input_evidence import (PROMPT_MARKER, native_external_panel_delivered, native_prompt_delivered,
                                  native_repeat_delivered)


def probe_backend() -> str:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-acceptance", action="store_true")
    args = parser.parse_args()
    if args.vm_acceptance:
        # The root-side acceptance harness already binds SSH to the disposable
        # guest UUID and verifies its installed provenance before copying us.
        if (os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1" or sys.platform != "linux"
                or not Path("/run/systemd/system").is_dir() or os.geteuid() == 0
                or pwd.getpwuid(os.geteuid()).pw_name != "exo"
                or os.environ.get("GDK_BACKEND") != "wayland"
                or not os.environ.get("WAYLAND_DISPLAY")
                or os.environ.get("GSK_RENDERER") != "cairo"
                or "gtk4-layer-shell" not in os.environ.get("LD_PRELOAD", "")):
            parser.error("requires the authenticated disposable Wayland VM as exo with layer-shell preloaded")
        return "wayland-layer"
    if (os.environ.get("GDK_BACKEND") != "broadway" or not os.environ.get("BROADWAY_DISPLAY")
            or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
            or os.environ.get("GSK_RENDERER")):
        parser.error("requires a private Broadway display or explicit disposable VM acceptance")
    return "broadway-private"


backend = probe_backend()  # refuse a working desktop before importing GTK
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
    for _ in range(100):
        if not ctx.pending():
            break
        ctx.iteration(False)


def wait_until(label, ready, seconds=5):
    """Let a real compositor complete focus and map round trips, with a hard bound."""
    deadline = time.monotonic() + seconds
    while True:
        pump()
        if ready():
            return
        if time.monotonic() >= deadline:
            timeout_snapshot(label)
            raise AssertionError(f"timed out after {seconds}s waiting for {label}")
        time.sleep(0.02)

def focus_within(window, target):
    focused = window.get_focus()
    while focused is not None:
        if focused == target:
            return True
        focused = focused.get_parent()
    return False


def timeout_snapshot(label):
    """Bounded failure evidence; a partial probe never emits a PASS result."""
    windows = {}
    surface = globals().get("ui")
    if surface is not None:
        for name in ("bar", "drawer", "card"):
            window = getattr(surface, name)
            try:
                focus = window.get_focus()
                path = []
                while focus is not None and len(path) < 24:
                    path.append(focus.__gtype__.name)
                    focus = focus.get_parent()
                native = window.get_surface()
                mode = A.LayerShell.get_keyboard_mode(window) if window.layered else None
                windows[name] = {"visible": window.get_visible(), "mapped": window.get_mapped(),
                    "gtk_active": window.is_active(), "gdk_state": int(native.get_state()) if native else None,
                    "focus_path": path, "keyboard_mode": str(mode),
                    "interactive": surface.drawer.interactive if name == "drawer" else None}
            except Exception as exc:
                windows[name] = {"error": type(exc).__name__}
    record = {"probe": "exo-surface-gtk-v2", "diagnostic": "timeout-snapshot", "status": "FAIL",
              "label": label, "windows": windows}
    payload = json.dumps(record, sort_keys=True)
    if len(payload.encode()) > 16384:
        payload = json.dumps({"probe": "exo-surface-gtk-v2", "status": "FAIL", "label": label,
                              "diagnostic": "timeout-snapshot-oversized"})
    print(payload, file=sys.stderr, flush=True)


def start_keys(*args):
    return subprocess.Popen(["wtype", *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def finish_keys(proc):
    try:
        wait_until("bounded wtype delivery", lambda: proc.poll() is not None, seconds=30)
        _, stderr = proc.communicate(timeout=1)
        assert proc.returncode == 0, stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def prompt_target_ready():
    # Gtk.is_active stays false after this layer surface remaps in the native
    # fixture even while real keys reach GtkText. This is only the local target
    # prerequisite; native checkpoints additionally prove keyboard delivery.
    return (ui.bar.get_visible() and ui.bar.get_mapped() and focus_within(ui.bar, ui.bar.entry)
            and (backend == "wayland-layer" or ui.bar.is_active()))


prompt_delivery = {"mode": "native", "checks": []} if backend == "wayland-layer" else {"mode": "synthetic"}


def check_prompt_delivery(stage, *, on_prefix=None):
    wait_until(stage + " mapped prompt target", prompt_target_ready)
    if backend != "wayland-layer":
        if on_prefix is not None:
            on_prefix()
        assert prompt_target_ready()
        return
    marker = PROMPT_MARKER  # no Enter, F6, Y or N; cannot answer a misplaced card
    old_text, old_position = ui.bar.entry.get_text(), ui.bar.entry.get_position()
    before, reviewed_before = len(sent), model.reviewed_approval
    events, injected, overflow = [], [], []
    ctl = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def observe(_ctl, keyval, code, _state):
        if len(events) < 64:
            events.append({"keyval": keyval, "code": code})
        else:
            overflow[:] = [True]
        return False

    def inject(entry):
        if not injected and entry.get_text() == marker[:4]:
            injected.append(True)
            on_prefix()

    ctl.connect("key-pressed", observe)
    ui.bar.add_controller(ctl)
    handler, proc = None, None
    try:
        ui.bar.entry.set_text("")
        if on_prefix is not None:
            handler = ui.bar.entry.connect("changed", inject)
        proc = start_keys("-d", "50", marker)
        finish_keys(proc)
        wait_until(stage + " actual prompt key delivery", lambda: ui.bar.entry.get_text() == marker
                   and model.text == marker)
        record = {"stage": stage, "text": ui.bar.entry.get_text(), "model_text": model.text,
                  "events": events, "sent_delta": len(sent) - before,
                  "review_unchanged": model.reviewed_approval is reviewed_before,
                  "reviewed": model.reviewed_approval is not None,
                  "entry_target": focus_within(ui.bar, ui.bar.entry), "mapped": ui.bar.get_mapped(),
                  "gtk_active": ui.bar.is_active(), "panel_update_injected": bool(injected)}
        assert not overflow and native_prompt_delivered(record), record
        assert reviewed_before is None, "a prompt keyboard checkpoint must not answer or enter review"
        if on_prefix is not None:
            assert injected, "the panel update must occur during actual typing"
        prompt_delivery["checks"].append(record)
    finally:
        if handler is not None:
            ui.bar.entry.disconnect(handler)
        ui.bar.remove_controller(ctl)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        ui.bar.entry.set_text(old_text)
        ui.bar.entry.set_position(old_position)


renderer_types = {}


def remember_renderer(name, window):
    renderer = window.get_renderer()  # Gtk.Native: the actual realized renderer, not just an environment hint
    assert renderer is not None, f"{name} has no realized renderer"
    renderer_types[name] = renderer.__gtype__.name
    if backend == "wayland-layer":
        assert renderer_types[name] == "GskCairoRenderer", renderer_types

SID = "s_1"
seq = iter(range(1, 1000))
def ev(body):
    return s.Event.of(body, next(seq), SID)

prof = Profile.default().put_app({"name": "WhatsApp", "url": "https://web.whatsapp.com/"})
model = SurfaceModel(prof)
model.set_session(SID)
sent, launched = [], []
ui = A.SurfaceUI(model, sent.append, launch=lambda name, fail: launched.append(name))
if backend == "wayland-layer":
    assert A.layer_shell_ok(), "the real Wayland compositor did not offer layer-shell"
    assert all(w.layered for w in (ui.bar, ui.drawer, ui.card)), "a surface window is not layered"
else:
    assert not any(w.layered for w in (ui.bar, ui.drawer, ui.card))
ui.on_connected(True)
ui.show()
wait_until("initial prompt", lambda: ui.bar.get_visible() and ui.bar.is_active())
remember_renderer("bar", ui.bar)
assert ui.bar.get_visible()
assert ui.drawer.box.get_focusable()

ui.bar.entry.set_text("whats")
pump()
row = ui.bar.suggestions.get_row_at_index(0)
assert row.get_child().get_text() == "Open WhatsApp"
ui.bar.entry.emit("activate")
wait_until("app launch hides prompt", lambda: launched == ["WhatsApp"] and not ui.bar.get_visible())
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
                   ("diff", {"files": [{"path": "a.txt", "diff": "-a\n+b"}]}),
                   ("file", {"path": "f.txt", "content": "hello"}),
                   ("markdown", {"markdown": "# Today\n- one"}),
                   ("approval", {"request_id": "a0", "summary": "Send?"})]:
    ui.on_event(ev(s.UiPanel(panel_id="p_" + kind, action="open", kind=kind, title=kind, text="text " + kind,
                             data=data)))
wait_until("unsolicited drawer mapped", lambda: ui.drawer.get_visible() and ui.drawer.get_mapped())
check_prompt_delivery("unsolicited-panels")
assert not ui.drawer.interactive and not ui.drawer.is_active()
assert prompt_target_ready()  # GTK focuses Entry's internal Gtk.Text on Broadway
remember_renderer("drawer", ui.drawer)
opened = [b.panel_id for b in sent if isinstance(b, s.UiRespond) and b.action == "opened"]
assert len(opened) == 8, opened
n = len(sent)
ui.bar.entry.set_text("panels")
ui.bar.entry.emit("activate")
wait_until("interactive panel focus", lambda: ui.drawer.interactive and ui.drawer.is_active()
           and focus_within(ui.drawer, ui.drawer.box))
assert ui.drawer.interactive and ui.drawer.is_active() and focus_within(ui.drawer, ui.drawer.box)
assert len(sent) == n
ui._drawer_key(None, A.Gdk.KEY_Escape, 0, 0)
wait_until("panel Escape closes drawer", lambda: not ui.drawer.get_visible())
check_prompt_delivery("panels-close")
assert not ui.drawer.interactive and not ui.drawer.get_visible()
assert prompt_target_ready()
ui.bar.entry.set_text("transcript")
ui.bar.entry.emit("activate")
wait_until("recent conversation focus", lambda: ui.drawer.mode == "transcript"
           and ui.drawer.is_active() and focus_within(ui.drawer, ui.drawer.transcript_view))
assert ui.drawer.interactive and ui.drawer.mode == "transcript"
assert ui.drawer.is_active() and focus_within(ui.drawer, ui.drawer.transcript_view)
buf = ui.drawer.transcript_view.get_buffer()
view = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
assert "Recent conversation" in view and "Only currently loaded history" in view
assert "Call Lead A." in view and len(sent) == n
assert not ui.drawer.transcript_view.get_editable()
buf.select_range(buf.get_start_iter(), buf.get_end_iter())
assert buf.get_has_selection()
ui.bar.entry.set_text("panels")
ui.bar.entry.emit("activate")
wait_until("transcript to panel focus", lambda: ui.drawer.mode == "panels"
           and ui.drawer.is_active() and focus_within(ui.drawer, ui.drawer.box))
assert ui.drawer.interactive and ui.drawer.mode == "panels"
assert ui.drawer.transcript_view is None
assert focus_within(ui.drawer, ui.drawer.box), (
    ui.drawer.get_focus(), ui.drawer.box, ui.drawer.is_active(), ui.bar.is_active())
assert len(sent) == n
choice_close = ui.drawer.panel_widgets["p_choice"].get_first_child().get_last_child()
choice_close.grab_focus()
assert focus_within(ui.drawer, choice_close)
focus_prompt = ui._focus_prompt
focus_calls = []
ui._focus_prompt = lambda: focus_calls.append("prompt")
ui.on_event(ev(s.UiPanel(panel_id="p_chart", action="update", kind="chart", title="chart",
                         text="updated chart", data={"labels": ["Mon"], "values": [4]})))
pump()
ui._focus_prompt = focus_prompt
assert focus_calls == []  # retained focus alone does not prove the bar was never presented
assert ui.drawer.panel_widgets["p_choice"].get_first_child().get_last_child() == choice_close
assert focus_within(ui.drawer, choice_close)  # another panel's refresh cannot displace this control
ui._drawer_key(None, A.Gdk.KEY_Escape, 0, 0)
check_prompt_delivery("updated-panels-close")
assert prompt_target_ready()
check_prompt_delivery("unsolicited-panel-update", on_prefix=lambda: ui.on_event(ev(s.UiPanel(
    panel_id="p_chart", action="update", kind="chart", title="chart", text="while typing",
    data={"labels": ["Mon"], "values": [5]}))))
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
shown = "\n".join(texts)
assert "Daily limit: $1.00. Monthly limit: $5.00." in shown, shown
assert "$0.000000001 today" in shown and "Unsettled calls: 1" in shown, shown
assert "administrator" in shown and not ui.card.get_visible()
assert len(sent) == n + 1  # inspection creates no turn or consent response
def unarmed_answer(decision):
    """Timing-only assertion has a frozen clock, independent of compositor focus latency."""
    original = A.GLib.get_monotonic_time
    try:
        A.GLib.get_monotonic_time = lambda: ui.card.shown_at
        before = len(sent)
        ui.card._answer(decision)
        assert len(sent) == before
    finally:
        A.GLib.get_monotonic_time = original


def review_with_f6():
    if backend == "wayland-layer":
        finish_keys(start_keys("-k", "F6"))
    else:
        assert ui._bar_key(None, A.Gdk.KEY_F6, 0, None)
        ui.card._key_released(None, A.Gdk.KEY_F6, 0, None)
    wait_until("explicit approval review", lambda: ui.card.get_visible()
               and ui.card.reviewing and focus_within(ui.card, ui.card.summary))
    remember_renderer("card", ui.card)


# Completion navigation must not turn into keyboard consent through Tab/Space.
ui.show()
ui.on_event(ev(s.ApprovalRequested(request_id="completion_tab", summary="Pending during completion",
                                 binding="completion-bound")))
ui.bar.entry.set_text("launcher draft")
ui.bar.entry.grab_focus()
assert ui.bar.review.get_visible() and not ui.bar.review.get_focusable()
before_tab = len(sent)
if backend == "wayland-layer":
    tab_events, tab_overflow = [], []
    tab_ctl = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def observe_tab_input(_ctl, keyval, code, _state):
        if len(tab_events) < 64:
            tab_events.append({"keyval": keyval, "code": code,
                               "review_target": focus_within(ui.bar, ui.bar.review)})
        else:
            tab_overflow[:] = [True]
        return False

    tab_ctl.connect("key-pressed", observe_tab_input)
    ui.bar.add_controller(tab_ctl)
    try:
        finish_keys(start_keys("-k", "Tab", "-k", "space", "-s", str(A.ARM_MS + 100), "-k", "y", "-k", "n"))
        required = {A.Gdk.KEY_Tab, A.Gdk.KEY_space, A.Gdk.KEY_y, A.Gdk.KEY_n}
        assert not tab_overflow and {row["keyval"] for row in tab_events} == required, tab_events
        assert all(row["code"] > 0 and not row["review_target"] for row in tab_events), tab_events
    finally:
        ui.bar.remove_controller(tab_ctl)
else:
    ui.bar.child_focus(Gtk.DirectionType.TAB_FORWARD)
    pump()
    assert not focus_within(ui.bar, ui.bar.review)
assert model.reviewed_approval is None and not ui.card.reviewing
assert not any(isinstance(body, s.ApprovalRespond) for body in sent[before_tab:])
ui.on_event(ev(s.ApprovalResolved(request_id="completion_tab", decision="deny")))
ui.bar.entry.set_text("")


# Wayland uses actual compositor keyboard delivery, including the late tod|ay boundary.
# Private Broadway has no compositor keyboard: it covers widget focus/model text only.
for index, (text, offset) in enumerate((("who are my calls today", 20), ("yesterday", 0),
                                      ("next meeting", 0), ("y", 0), ("n", 0))):
    rid = f"typing_{index}"
    ui.bar.entry.set_text("")
    before = len(sent)
    proc, injected, handler = None, [], None
    request = s.ApprovalRequested(request_id=rid, summary="Read this request", binding=rid)
    if backend == "wayland-layer" and offset:
        def inject_at_prefix(entry):
            if not injected and len(entry.get_text()) == offset:
                injected.append(entry.get_text())
                ui.on_event(ev(request))
        handler = ui.bar.entry.connect("changed", inject_at_prefix)
        proc = start_keys("-d", "150", text)
        try:
            wait_until("actual tod|ay request injection", lambda: bool(injected), seconds=30)
        except BaseException:
            proc.kill()
            proc.wait(timeout=2)
            ui.bar.entry.disconnect(handler)
            raise
        assert injected == [text[:offset]]
    else:
        if offset:
            ui.bar.entry.set_text(text[:offset])
        ui.on_event(ev(request))
    wait_until("pending request retains mapped prompt target", prompt_target_ready)
    assert ui.bar.review.get_visible() and ui.bar.review.get_label() == "Review request (F6)"
    assert not ui.card.reviewing and model.reviewed_approval is None
    assert ui.card.get_visible() == (backend == "wayland-layer")
    if backend == "wayland-layer":
        try:
            finish_keys(proc or start_keys("-d", "150", text))
        finally:
            if handler is not None:
                ui.bar.entry.disconnect(handler)
    else:
        for char in text[offset:]:
            ui._bar_key(None, A.Gdk.keyval_from_name(char) or 0, 0, None)
            ui.bar.entry.set_text(ui.bar.entry.get_text() + char)
            ui.card._key_released(None, A.Gdk.keyval_from_name(char) or 0, 0, None)
            pump()
    assert ui.bar.entry.get_text() == text and model.text == text
    assert len(sent) == before and prompt_target_ready()
    ui.on_event(ev(s.ApprovalResolved(request_id=rid, decision="deny")))
ui.bar.entry.set_text("")
ui.on_event(ev(s.ApprovalRequested(request_id="a1", summary="Send this message to Ahmed?", binding="h1")))
assert model.reviewed_approval is None
review_with_f6()
assert ui.card.summary.get_text() == "Send this message to Ahmed?"
n = len(sent)
unarmed_answer("allow")
assert ui.card.get_visible()
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
for modifier in (A.Gdk.ModifierType.CONTROL_MASK, A.Gdk.ModifierType.ALT_MASK,
                 A.Gdk.ModifierType.SUPER_MASK, A.Gdk.ModifierType.META_MASK):
    ui.card._key(None, A.Gdk.KEY_y, 0, modifier)
    assert len(sent) == n and ui.card.get_visible()
    ui.card._key_released(None, A.Gdk.KEY_y, 0, modifier)
if backend == "wayland-layer":
    finish_keys(start_keys("-k", "y"))
    # Retain the separate synthetic held-key test after real delivery released Y.
    ui.card.observe_key_press(A.Gdk.KEY_y, 0)
else:
    ui.card._key(None, A.Gdk.KEY_y, 0, None)
wait_until("answer exits review", lambda: not ui.card.reviewing)
assert sent[-1] == s.ApprovalRespond(request_id="a1", decision="allow", client="exo-surface", binding="h1")
assert len(sent) == n + 1
ui.on_event(ev(s.ApprovalRequested(request_id="a2", summary="Delete everything?", binding="h2")))
n = len(sent)
assert not ui.card.reviewing
ui.card._key(None, A.Gdk.KEY_y, 0, None)  # held Y cannot answer a newly pending request
assert len(sent) == n and model.reviewed_approval is None
review_with_f6()
unarmed_answer("allow")
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
ui.card._key(None, A.Gdk.KEY_y, 0, None)  # held Y remains held even after explicit review
assert len(sent) == n
ui.card._key_released(None, A.Gdk.KEY_y, 0, None)
ui.card._key(None, A.Gdk.KEY_Escape, 0, None)
wait_until("approval Escape closes card", lambda: not ui.card.get_visible())
check_prompt_delivery("approval-defer")
assert len(sent) == n and model.approvals[-1].request_id == "a2"
ui.show()  # a generic prompt opening is not a consent gesture
assert model.reviewed_approval is None and not ui.card.get_visible()
ui.bar.entry.set_text("yesterday")
assert len(sent) == n and model.text == "yesterday"
ui.bar.entry.set_text("")
review_with_f6()
assert ui.card.summary.get_text() == "Delete everything?"
unarmed_answer("deny")
ui.on_event(ev(s.ApprovalResolved(request_id="a2", decision="deny")))

# Held F6 must not become a fresh gesture for the next request after A is answered.
ui.show()
ui.on_event(ev(s.ApprovalRequested(request_id="held_f6_a", summary="First held-F6 request", binding="fa")))
ui.on_event(ev(s.ApprovalRequested(request_id="held_f6_b", summary="Second held-F6 request", binding="fb")))
n = len(sent)
held_f6_evidence = {"mode": "synthetic"}
if backend == "wayland-layer":
    # One virtual keyboard retains F6 while Y is pressed/released and a repeat is delivered.
    # Exiting wtype itself releases held keys, so this must remain one invocation.
    native_events, overflow = [], []
    original_review_key = ui.card.fresh_review_key

    def observe_review_key(keyval, code, state):
        pending, reviewed = model.approval, model.reviewed_approval
        blocked = (A.Gdk.ModifierType.SHIFT_MASK | A.Gdk.ModifierType.CONTROL_MASK |
                   A.Gdk.ModifierType.ALT_MASK | A.Gdk.ModifierType.SUPER_MASK | A.Gdk.ModifierType.META_MASK)
        row = {"pending_id": pending.request_id if pending else None,
               "reviewed_id": reviewed.request_id if reviewed else None, "code": code,
               "held_before": code in ui.card.review_keys, "blocked": bool(state and state & blocked)}
        row["accepted"] = None
        if len(native_events) < 128:
            native_events.append(row)
        else:
            overflow[:] = [True]
        accepted = original_review_key(keyval, code, state)  # call the original after recording input
        row["accepted"] = accepted
        return accepted

    ui.card.fresh_review_key = observe_review_key
    proc = None
    try:
        proc = start_keys("-P", "F6", "-s", "1200", "-k", "y", "-s", "800",
                          "-P", "F6", "-s", "800", "-d", "150", "yesterday", "-p", "F6")
        wait_until("held F6 enters only first review", lambda: model.reviewed_approval is not None
                   and model.reviewed_approval.request_id == "held_f6_a" and ui.card.is_active())
        ui.card.shown_at -= (A.ARM_MS + 50) * 1000
        finish_keys(proc)
        initial = next((row for row in native_events if row["pending_id"] == "held_f6_a"
                        and row["accepted"] and row["code"] > 0), None)
        assert initial is not None, native_events
        wait_until("observed F6 release", lambda: initial["code"] not in ui.card.review_keys)
        held_f6_evidence = {"mode": "native", "events": native_events,
                            "released": initial["code"] not in ui.card.review_keys}
        assert not overflow and native_repeat_delivered(native_events, released=held_f6_evidence["released"]), native_events
    finally:
        ui.card.fresh_review_key = original_review_key
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
else:
    assert ui._bar_key(None, A.Gdk.KEY_F6, 77, None)
    wait_until("held F6 enters first fallback review", lambda: ui.card.reviewing and ui.card.is_active())
    armed_at = ui.card.shown_at
    ui.card._key(None, A.Gdk.KEY_F6, 77, None)
    assert ui.card.shown_at == armed_at  # a repeat does not reset the current review's guard
    ui.card.shown_at -= (A.ARM_MS + 50) * 1000
    ui.card._key(None, A.Gdk.KEY_y, 78, None)
    ui.card._key_released(None, A.Gdk.KEY_y, 78, None)
    assert ui._bar_key(None, A.Gdk.KEY_F6, 77, None)
    assert model.reviewed_approval is None
    ui.bar.entry.set_text("yesterday")  # fallback covers widgets; Wayland above supplies real keys
    ui.card._key_released(None, A.Gdk.KEY_F6, 77, None)
assert len(sent) == n + 1 and sent[-1] == s.ApprovalRespond(
    request_id="held_f6_a", decision="allow", client="exo-surface", binding="fa")
assert model.reviewed_approval is None and model.approval.request_id == "held_f6_b"
assert ui.bar.entry.get_text() == "yesterday" and model.text == "yesterday"
if backend == "wayland-layer":
    held_f6_evidence.update({
        "a_allowed": sum(isinstance(body, s.ApprovalRespond) and body.request_id == "held_f6_a"
                         and body.decision == "allow" for body in sent[n:]),
        "b_responses": sum(isinstance(body, s.ApprovalRespond) and body.request_id == "held_f6_b"
                           for body in sent[n:]),
        "reviewed": model.reviewed_approval is not None, "prompt_text": ui.bar.entry.get_text()})
ui.bar.entry.set_text("")
review_with_f6()  # only the observed release permits a new gesture for B
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
if backend == "wayland-layer":
    finish_keys(start_keys("-k", "n"))
else:
    ui.card._key(None, A.Gdk.KEY_n, 0, None)
    ui.card._key_released(None, A.Gdk.KEY_n, 0, None)
assert len(sent) == n + 2 and sent[-1] == s.ApprovalRespond(
    request_id="held_f6_b", decision="deny", client="exo-surface", binding="fb")
ui.on_event(ev(s.ApprovalResolved(request_id="held_f6_a", decision="allow")))
ui.on_event(ev(s.ApprovalResolved(request_id="held_f6_b", decision="deny")))

# A modified F6 press cannot become unmodified review through autorepeat after modifier release.
ui.on_event(ev(s.ApprovalRequested(request_id="modified_f6", summary="Modifier request")))
for modifier in (A.Gdk.ModifierType.SHIFT_MASK, A.Gdk.ModifierType.CONTROL_MASK,
                 A.Gdk.ModifierType.ALT_MASK, A.Gdk.ModifierType.SUPER_MASK, A.Gdk.ModifierType.META_MASK):
    n = len(sent)
    assert ui._bar_key(None, A.Gdk.KEY_F6, 79, modifier)
    assert ui._drawer_key(None, A.Gdk.KEY_F6, 79, None)  # same held key after modifier release
    assert model.reviewed_approval is None and len(sent) == n
    ui.card._key_released(None, A.Gdk.KEY_F6, 79, None)
ui.on_event(ev(s.ApprovalResolved(request_id="modified_f6", decision="deny")))

# An old widget's queued click cannot answer a replacement or the next request.
ui.on_event(ev(s.ApprovalRequested(request_id="stale_a", summary="Old request", binding="old")))
old_yes = ui.card.buttons.get_first_child()
old_token = ui.card.token
ui.on_event(ev(s.ApprovalRequested(request_id="stale_b", summary="Next request", binding="new")))
ui.on_event(ev(s.ApprovalResolved(request_id="stale_a", decision="deny")))
n = len(sent)
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
old_yes.emit("clicked")
ui._answer("allow", old_token, explicit_click=True)
ui._answer("allow", None, explicit_click=True)  # an untargeted activation cannot select the new request
assert len(sent) == n and model.approval.request_id == "stale_b"
if backend == "broadway-private":
    review_with_f6()  # fallback never maps an unreviewed standalone card
    ui.card.shown_at -= (A.ARM_MS + 50) * 1000
ui.card.buttons.get_first_child().emit("clicked")  # a targeted click on the displayed exact request
assert sent[-1] == s.ApprovalRespond(request_id="stale_b", decision="allow", client="exo-surface", binding="new")
assert len(sent) == n + 1 and model.reviewed_approval is None
ui.on_event(ev(s.ApprovalResolved(request_id="stale_b", decision="allow")))

# Same request ID and empty binding still cannot transfer a review to new content.
ui.on_event(ev(s.ApprovalRequested(request_id="changed", summary="First text")))
review_with_f6()
old_yes = ui.card.buttons.get_first_child()
ui.on_event(ev(s.ApprovalRequested(request_id="changed", summary="Changed text")))
n = len(sent)
assert not ui.card.reviewing and model.reviewed_approval is None
old_yes.emit("clicked")
assert len(sent) == n
ui.on_event(ev(s.ApprovalResolved(request_id="changed", decision="deny")))

# Losing activation after actual review revokes keyboard consent without refocusing the bar.
ui.show()
ui.on_event(ev(s.ApprovalRequested(request_id="focus_loss", summary="Focus loss request", binding="focus")))
review_with_f6()
wait_until("review actually active", lambda: ui.card.is_active())
other = Gtk.Window(title="Synthetic other work")
if backend == "wayland-layer":
    assert A._layer(other, "OVERLAY", (), "EXCLUSIVE", "test-other")
other_entry = Gtk.Entry()
other.set_child(other_entry)
other.set_default_size(400, 100)
other.present()
other_entry.grab_focus()
wait_until("external fixture focus revokes review", lambda: other.is_active()
           and model.reviewed_approval is None and not ui.card.reviewing)
n = len(sent)
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
ui.card._key(None, A.Gdk.KEY_y, 0, None)
external_panel_update = {"mode": "synthetic"}
if backend == "wayland-layer":
    external_keys, external_injected, external_overflow = [], [], []
    prompt_before, model_before = ui.bar.entry.get_text(), model.text
    external_ctl = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def observe_external(_ctl, keyval, code, _state):
        if len(external_keys) < 64:
            external_keys.append({"keyval": keyval, "code": code})
        else:
            external_overflow[:] = [True]
        return False

    def panel_during_external_input(entry):
        if not external_injected and entry.get_text() == "yest":
            external_injected.append(True)
            ui.on_event(ev(s.UiPanel(panel_id="p_chart", action="update", kind="chart", title="chart",
                                    text="while working elsewhere", data={"labels": ["Mon"], "values": [6]})))

    external_ctl.connect("key-pressed", observe_external)
    other.add_controller(external_ctl)
    external_handler = other_entry.connect("changed", panel_during_external_input)
    try:
        finish_keys(start_keys("-d", "150", "yesterday"))
        wait_until("panel update preserves actual external typing", lambda: other_entry.get_text() == "yesterday")
        external_panel_update = {"mode": "native", "text": other_entry.get_text(), "events": external_keys,
            "panel_update_injected": bool(external_injected), "sent_delta": len(sent) - n,
            "reviewed": model.reviewed_approval is not None, "external_active": other.is_active(),
            "external_target": focus_within(other, other_entry),
            "prompt_unchanged": ui.bar.entry.get_text() == prompt_before and model.text == model_before}
        assert not external_overflow and native_external_panel_delivered(external_panel_update), external_panel_update
    finally:
        other_entry.disconnect(external_handler)
        other.remove_controller(external_ctl)
    assert other_entry.get_text() == "yesterday"
else:
    ui.on_event(ev(s.UiPanel(panel_id="p_chart", action="update", kind="chart", title="chart",
                            text="while working elsewhere", data={"labels": ["Mon"], "values": [6]})))
    pump()
assert len(sent) == n and other.is_active()
ui.on_event(ev(s.ApprovalRequested(request_id="external_pending", summary="Another pending request")))
wait_until("new pending request preserves other window focus", lambda: other.is_active())
ui.on_event(ev(s.ApprovalResolved(request_id="external_pending", decision="deny")))
assert other.is_active() and len(sent) == n
other.destroy()
ui.show()
check_prompt_delivery("external-work-return")
review_with_f6()
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
ui.card._key(None, A.Gdk.KEY_n, 0, None)
assert sent[-1] == s.ApprovalRespond(request_id="focus_loss", decision="deny", client="exo-surface", binding="focus")
assert len(sent) == n + 1
ui.card._key_released(None, A.Gdk.KEY_n, 0, None)
ui.on_event(ev(s.ApprovalResolved(request_id="focus_loss", decision="deny")))
generic_payload = "<b>literal</b>" + "long body " * 1200 + "GENERIC_SUFFIX"
generic_call = s.ToolCall(id="c_generic", name="send_message", arguments={
    "target": "Ahmed", "message": generic_payload})
ui.on_event(ev(s.ApprovalRequested(request_id="a_generic", summary="Send?", call=generic_call,
                                 binding="generic_bound")))
review_with_f6()
wait_until("full generic approval details", lambda: ui.card.detail_scroll.get_visible()
           and focus_within(ui.card, ui.card.summary))
assert ui.card.detail_scroll.get_visible() and focus_within(ui.card, ui.card.summary)
buf = ui.card.detail.get_buffer()
details = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
assert json.loads(details.split("\n", 1)[1])["arguments"]["message"] == generic_payload
assert "GENERIC_SUFFIX" in details and "<b>literal</b>" in details
buf.select_range(buf.get_start_iter(), buf.get_end_iter())
assert buf.get_has_selection()
n = len(sent)
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
ui.card._key(None, A.Gdk.KEY_y, 0, None)  # fresh press after the observed release
check_prompt_delivery("generic-answer-return")
assert sent[-1] == s.ApprovalRespond(request_id="a_generic", decision="allow",
                                      client="exo-surface", binding="generic_bound")
assert len(sent) == n + 1 and prompt_target_ready()
ui.card._key_released(None, A.Gdk.KEY_y, 0, None)
ui.on_event(ev(s.ApprovalResolved(request_id="a_generic", decision="allow")))
from exo_os.egress import policy
origin = "https://api.example.test"
policy.load_policy = lambda: {"messages": policy.Profile(origin, ("POST",), "/messages", "hidden_token")}
payload = "<b>literal markup</b>" + "full body " * 1200 + "PAYLOAD_SUFFIX"
call = s.ToolCall(id="c_outbound", name="credential_request", arguments={
    "profile": "messages", "request_id": "operation_123", "expected_origin": origin,
    "method": "POST", "path": "/messages/send", "body": payload})
ui.on_event(ev(s.ApprovalRequested(request_id="a3", summary="Misleading recipient", call=call,
                                 binding="bound_origin_and_payload")))
review_with_f6()
wait_until("credential approval detail presentation", lambda: ui.card.detail_scroll.get_visible()
           and focus_within(ui.card, ui.card.summary))
assert ui.card.detail_scroll.get_visible() and isinstance(ui.card.detail, Gtk.TextView)
assert not ui.card.detail.get_editable() and ui.card.detail.get_cursor_visible()
buf = ui.card.detail.get_buffer()
text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
assert json.loads(text.split("\n", 1)[1])["request"]["body"] == payload
assert "PAYLOAD_SUFFIX" in text and origin in ui.card.summary.get_text()
assert ui.card.detail_scroll.get_max_content_height() == 360
buf.select_range(buf.get_start_iter(), buf.get_end_iter())
assert buf.get_has_selection()  # full request can be selected and copied
assert ui.card.options == ["allow", "deny"]
ui.on_event(ev(s.ApprovalResolved(request_id="a3", decision="deny")))
ui.show(prof.set_value("surface.language", "ur"))
wait_until("RTL profile refresh", lambda: all(w.get_direction() == Gtk.TextDirection.RTL
           for w in (ui.bar, ui.drawer, ui.card)))
assert all(w.get_direction() == Gtk.TextDirection.RTL for w in (ui.bar, ui.drawer, ui.card))
ui.show(prof)
wait_until("LTR profile refresh", lambda: all(w.get_direction() == Gtk.TextDirection.LTR
           for w in (ui.bar, ui.drawer, ui.card)))
assert all(w.get_direction() == Gtk.TextDirection.LTR for w in (ui.bar, ui.drawer, ui.card))
ui.hide()
wait_until("surface hides", lambda: not ui.bar.get_visible() and not ui.drawer.get_visible())
assert not ui.bar.get_visible() and not ui.drawer.get_visible()
print(json.dumps({"probe": "exo-surface-gtk-v2", "backend": backend,
                  "layered": all(w.layered for w in (ui.bar, ui.drawer, ui.card)),
                  "gsk_renderer": os.environ.get("GSK_RENDERER", "default"), "renderers": renderer_types,
                  "held_f6": held_f6_evidence, "prompt_delivery": prompt_delivery,
                  "external_panel_update": external_panel_update,
                  "status": "PASS"}, sort_keys=True))

#!/usr/bin/env python3
"""Synthetic GTK surface checks on private Broadway or an authenticated disposable Wayland VM.

This creates widgets with fake protocol events only. It does not connect to exod,
send a model request, launch an app, or read a credential.
"""
import argparse
import json
import os
import pwd
import sys
import time
from pathlib import Path


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
                or "gtk4-layer-shell" not in os.environ.get("LD_PRELOAD", "")):
            parser.error("requires the authenticated disposable Wayland VM as exo with layer-shell preloaded")
        return "wayland-layer"
    if (os.environ.get("GDK_BACKEND") != "broadway" or not os.environ.get("BROADWAY_DISPLAY")
            or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
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
            raise AssertionError(f"timed out after {seconds}s waiting for {label}")
        time.sleep(0.02)

def focus_within(window, target):
    focused = window.get_focus()
    while focused is not None:
        if focused == target:
            return True
        focused = focused.get_parent()
    return False

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
wait_until("unsolicited drawer without focus transfer", lambda: ui.drawer.get_visible()
           and ui.bar.is_active() and focus_within(ui.bar, ui.bar.entry))
assert not ui.drawer.interactive and ui.bar.is_active() and not ui.drawer.is_active()
assert focus_within(ui.bar, ui.bar.entry)  # GTK focuses Entry's internal Gtk.Text on Broadway
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
wait_until("panel Escape restores prompt", lambda: not ui.drawer.get_visible()
           and ui.bar.is_active() and focus_within(ui.bar, ui.bar.entry))
assert not ui.drawer.interactive and not ui.drawer.get_visible()
assert ui.bar.is_active() and focus_within(ui.bar, ui.bar.entry)
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
wait_until("panel Escape after update", lambda: ui.bar.is_active()
           and focus_within(ui.bar, ui.bar.entry))
assert ui.bar.is_active() and focus_within(ui.bar, ui.bar.entry)
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
ui.on_event(ev(s.ApprovalRequested(request_id="a1", summary="Send this message to Ahmed?", binding="h1")))
wait_until("approval safe first focus", lambda: ui.card.get_visible()
           and focus_within(ui.card, ui.card.summary))
assert ui.card.get_visible() and ui.card.summary.get_text() == "Send this message to Ahmed?"
assert focus_within(ui.card, ui.card.summary)  # safe first focus, before any consent button
n = len(sent)
ui.card._answer("allow")  # too soon after it appeared: ignored
assert len(sent) == n and ui.card.get_visible()
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
for modifier in (A.Gdk.ModifierType.CONTROL_MASK, A.Gdk.ModifierType.ALT_MASK,
                 A.Gdk.ModifierType.SUPER_MASK, A.Gdk.ModifierType.META_MASK):
    ui.card._key(None, A.Gdk.KEY_y, 0, modifier)
    assert len(sent) == n and ui.card.get_visible()
    ui.card._key_released(None, A.Gdk.KEY_y, 0, modifier)
ui.card._key(None, A.Gdk.KEY_y, 0, None)
wait_until("approved card hides", lambda: not ui.card.get_visible())
assert sent[-1] == s.ApprovalRespond(request_id="a1", decision="allow", client="exo-surface", binding="h1")
assert not ui.card.get_visible()

ui.on_event(ev(s.ApprovalRequested(request_id="a2", summary="Delete everything?", binding="h2")))
wait_until("second approval safe first focus", lambda: ui.card.get_visible()
           and focus_within(ui.card, ui.card.summary))
assert ui.card.get_visible() and ui.card.summary.get_text() == "Delete everything?"
n = len(sent)
ui.card._answer("allow")  # a key still held from the last answer: the new card is not armed yet
assert len(sent) == n and ui.card.get_visible()
ui.card.shown_at -= (A.ARM_MS + 50) * 1000
ui.card._key(None, A.Gdk.KEY_y, 0, None)  # held Y repeats after the arm interval
assert len(sent) == n and ui.card.get_visible()
ui.card._key_released(None, A.Gdk.KEY_y, 0, None)

ui.card._key(None, A.Gdk.KEY_Escape, 0, None)  # dismissal works even before arming
wait_until("approval Escape defers", lambda: not ui.card.get_visible())
assert len(sent) == n and not ui.card.get_visible()
assert model.approvals[-1].request_id == "a2" and not model.answer_check
ui.show()
wait_until("deferred approval reopens", lambda: ui.card.get_visible()
           and focus_within(ui.card, ui.card.summary))
assert ui.card.get_visible() and ui.card.summary.get_text() == "Delete everything?"
ui.card._answer("deny")
assert len(sent) == n  # reopening also gets the anti-misclick arm window
ui.on_event(ev(s.ApprovalResolved(request_id="a2", decision="deny")))
generic_payload = "<b>literal</b>" + "long body " * 1200 + "GENERIC_SUFFIX"
generic_call = s.ToolCall(id="c_generic", name="send_message", arguments={
    "target": "Ahmed", "message": generic_payload})
ui.on_event(ev(s.ApprovalRequested(request_id="a_generic", summary="Send?", call=generic_call,
                                 binding="generic_bound")))
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
wait_until("generic approval restores prompt", lambda: ui.bar.is_active()
           and focus_within(ui.bar, ui.bar.entry))
assert sent[-1] == s.ApprovalRespond(request_id="a_generic", decision="allow",
                                      client="exo-surface", binding="generic_bound")
assert len(sent) == n + 1 and ui.bar.is_active() and focus_within(ui.bar, ui.bar.entry)
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
print(json.dumps({"probe": "exo-surface-gtk-v1", "backend": backend,
                  "layered": all(w.layered for w in (ui.bar, ui.drawer, ui.card)),
                  "status": "PASS"}, sort_keys=True))

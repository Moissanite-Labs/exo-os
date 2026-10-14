"""The surface's windows, drawn with GTK 4 and gtk4-layer-shell from a SurfaceModel (DESIGN.md §7.2).

Three shapes: the prompt bar on the top edge, the panel drawer on the right, the approval card in the
middle. Each is a layer surface when the compositor offers layer-shell (and the library was preloaded, see
`app`), else a plain undecorated window, which is how the tests draw them on a private broadway display.
"""
from __future__ import annotations

import os
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from exo.protocol import schema as s  # noqa: E402

from ..profile import Profile  # noqa: E402
from .model import Line, SurfaceModel, approval_key, approval_token, panel_view  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):  # library not installed: plain windows
    LayerShell = None

ARM_MS = 700  # an approval card ignores keys and clicks this long, so a key already being typed is not a Yes
DRAWER_WIDTH = 460
PLACEHOLDER = "Ask Exo, or type an app name"
YES, NO, ALWAYS = "Yes", "No", "Always"

CSS = """
window.exo { background-color: #fbfaf7; color: #1f2328; font-size: 20px; }
window.exo-bar { border-bottom: 1px solid #d9d4ca; }
window.exo-drawer { border-left: 1px solid #d9d4ca; }
window.exo-card { border: 2px solid #c9c2b5; border-radius: 16px; }
entry.prompt { font-size: 26px; min-height: 54px; padding: 0 16px; border-radius: 12px;
  background-color: #ffffff; border: 1px solid #cdc7bb; }
label.status { color: #5a6270; font-size: 18px; }
list.suggestions { background: transparent; }
list.suggestions row { padding: 8px 16px; border-radius: 10px; }
list.suggestions row:selected { background-color: #e7eef8; color: #1f2328; }
label.you { color: #5a6270; }
label.exo { color: #1f2328; }
label.note { color: #6b7280; font-style: italic; }
label.error { color: #9a3b26; }
label.tool { color: #6b7280; font-family: monospace; font-size: 16px; }
label.panel-title { font-weight: bold; font-size: 22px; }
label.panel-text { color: #4b5260; }
label.card-summary { font-size: 28px; }
label.card-detail { color: #6b7280; font-family: monospace; font-size: 16px; }
button.big { font-size: 24px; min-height: 56px; min-width: 140px; border-radius: 12px; }
button.yes { background: #2f6f4f; color: #ffffff; }
.mono, textview.mono text { font-family: monospace; font-size: 16px; }
.panel { padding: 12px 0; border-bottom: 1px solid #e6e1d8; }
"""


def spawn_app(name: str, on_fail: Callable[[str], None]) -> None:
    """`exo-os app open <Name>`: the same command the app keys run, so the bar and the keys agree."""
    try:
        pid, *_ = GLib.spawn_async(["exo-os", "app", "open", name],
                                   flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD)
    except GLib.Error:
        on_fail(name)
        return

    def done(pid: int, status: int) -> None:
        GLib.spawn_close_pid(pid)
        if os.waitstatus_to_exitcode(status) != 0:
            on_fail(name)

    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, done)


# -- windows


def layer_shell_ok() -> bool:
    """Layer-shell needs a Wayland display whose compositor offers it (asking on another backend logs a
    critical warning, so the display type is checked first)."""
    display = Gdk.Display.get_default()
    if LayerShell is None or display is None or display.__gtype__.name != "GdkWaylandDisplay":
        return False
    return bool(LayerShell.is_supported())


def _layer(win: Gtk.Window, layer: str, anchors: tuple[str, ...], keyboard: str, name: str) -> bool:
    """Make win a layer surface; False (and a plain window) where layer-shell is not supported."""
    if not layer_shell_ok():
        win.set_decorated(False)
        return False
    LayerShell.init_for_window(win)
    LayerShell.set_namespace(win, f"exo-surface-{name}")  # Hyprland layer rules and tests tell them apart
    LayerShell.set_layer(win, getattr(LayerShell.Layer, layer))
    for edge in anchors:
        LayerShell.set_anchor(win, getattr(LayerShell.Edge, edge), True)
    LayerShell.set_keyboard_mode(win, getattr(LayerShell.KeyboardMode, keyboard))
    return True


def _keyboard(win: Gtk.Window, mode: str) -> None:
    if LayerShell is not None and LayerShell.is_layer_window(win):
        LayerShell.set_keyboard_mode(win, getattr(LayerShell.KeyboardMode, mode))


def _label(text: str, *classes: str, wrap: bool = True) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=0.0, wrap=wrap, css_classes=list(classes))
    lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return lab


def _clear(box: Gtk.Box) -> None:
    while (child := box.get_first_child()) is not None:
        box.remove(child)


class Bar(Gtk.Window):
    """The prompt bar: one large line at the top edge; suggestions and the transcript open below it."""

    def __init__(self) -> None:
        super().__init__(title="Exo", css_classes=["exo", "exo-bar"])
        self.layered = _layer(self, "OVERLAY", ("TOP", "LEFT", "RIGHT"), "EXCLUSIVE", "bar")
        if not self.layered:
            self.set_default_size(960, -1)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                        margin_start=24, margin_end=24)
        row = Gtk.Box(spacing=16)
        self.entry = Gtk.Entry(placeholder_text=PLACEHOLDER, hexpand=True, css_classes=["prompt"])
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [PLACEHOLDER])
        self.status = _label("", "status", wrap=False)
        # Completion Tab/Space must not become a review gesture while typing.
        # Mouse clicks and the dedicated F6 shortcut remain explicit review paths.
        self.review = Gtk.Button(label="Review request (F6)", visible=False, focusable=False)
        self.pending = _label("", "status")
        self.pending.set_visible(False)
        row.append(self.entry)
        row.append(self.status)
        row.append(self.review)
        self.suggestions = Gtk.ListBox(css_classes=["suggestions"], visible=False)
        self.suggestions.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.scroll = Gtk.ScrolledWindow(child=self.lines, visible=False, propagate_natural_height=True,
                                         max_content_height=420)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        for w in (row, self.pending, self.suggestions, self.scroll):
            outer.append(w)
        self.set_child(outer)

    def show_suggestions(self, items: list[Any], selected: int) -> None:
        _clear_list(self.suggestions)
        for item in items:
            self.suggestions.append(_label(item.label, wrap=False))
        self.suggestions.set_visible(bool(items))
        if items:
            self.suggestions.select_row(self.suggestions.get_row_at_index(selected))

    def show_transcript(self, lines: list[Line], streaming: str) -> None:
        _clear(self.lines)
        for ln in lines:
            self.lines.append(_label(ln.text, ln.role))
        if streaming:
            self.lines.append(_label(streaming, "exo"))
        self.scroll.set_visible(bool(lines or streaming))
        adj = self.scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) or False)


def _clear_list(lb: Gtk.ListBox) -> None:
    lb.remove_all()


class Drawer(Gtk.Window):
    """Panels on the right edge. Each panel shows its widget and always its plain text form."""

    def __init__(self) -> None:
        super().__init__(title="Exo panels", css_classes=["exo", "exo-drawer"])
        self.layered = _layer(self, "TOP", ("RIGHT", "TOP", "BOTTOM"), "NONE", "drawer")
        self.interactive = False
        self.mode = "panels"
        self.transcript_view: Gtk.TextView | None = None
        self.panel_widgets: dict[str, Gtk.Widget] = {}
        self.panel_views: dict[str, dict[str, Any]] = {}
        self.set_default_size(DRAWER_WIDTH, 600 if not self.layered else -1)
        self.set_size_request(DRAWER_WIDTH, -1)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=16, margin_bottom=16,
                           margin_start=20, margin_end=20)
        self.box.set_can_focus(True)
        self.box.set_focusable(True)
        self.box.update_property([Gtk.AccessibleProperty.LABEL], ["Panels"])
        self.set_child(Gtk.ScrolledWindow(child=self.box, hscrollbar_policy=Gtk.PolicyType.NEVER))

    def below(self, height: int) -> None:
        """Sit under the prompt bar, which is on the overlay layer and as wide as the screen."""
        if self.layered and height > 0:
            LayerShell.set_margin(self, LayerShell.Edge.TOP, height)

    def show_panels(self, views: list[dict[str, Any]], on_close: Callable[[str], None],
                    on_choice: Callable[[str, str], None]) -> None:
        self.mode = "panels"
        self.transcript_view = None
        if not views:
            _clear(self.box)
            self.panel_widgets.clear()
            self.panel_views.clear()
            self.box.append(_label("No panels open."))
            return
        wanted = {v["panel_id"] for v in views}
        for pid in set(self.panel_widgets) - wanted:
            widget = self.panel_widgets.pop(pid)
            if widget.get_parent() is self.box:
                self.box.remove(widget)
            self.panel_views.pop(pid, None)
        if not self.panel_widgets:
            _clear(self.box)  # a placeholder or the previous local reading view
        previous: Gtk.Widget | None = None
        for view in views:
            pid = view["panel_id"]
            widget = self.panel_widgets.get(pid)
            if widget is None or self.panel_views[pid] != view:
                if widget is not None and widget.get_parent() is self.box:
                    self.box.remove(widget)
                widget = panel_widget(view, on_close, on_choice)
                self.panel_widgets[pid] = widget
                self.panel_views[pid] = view
            if widget.get_parent() is not self.box:
                self.box.insert_child_after(widget, previous)
            elif widget.get_prev_sibling() is not previous:
                self.box.reorder_child_after(widget, previous)
            previous = widget

    def show_recent_conversation(self, text: str, on_close: Callable[[], None]) -> None:
        self.mode = "transcript"
        _clear(self.box)
        self.panel_widgets.clear()
        self.panel_views.clear()
        self.box.append(_label("Recent conversation", "panel-title"))
        view = Gtk.TextView(editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.update_property([Gtk.AccessibleProperty.LABEL], ["Recent conversation, loaded lines only"])
        view.get_buffer().set_text(text)
        self.transcript_view = view
        self.box.append(Gtk.ScrolledWindow(child=view, min_content_height=300, vexpand=True))
        close = Gtk.Button(label="Close recent conversation")
        close.connect("clicked", lambda *_: on_close())
        self.box.append(close)


def panel_widget(v: dict[str, Any], on_close: Callable[[str], None],
                 on_choice: Callable[[str, str], None]) -> Gtk.Widget:
    pid, kind = v["panel_id"], v["kind"]
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["panel"])
    head = Gtk.Box(spacing=8)
    head.append(_label(v.get("title") or "", "panel-title"))
    head.get_first_child().set_hexpand(True)
    close = Gtk.Button(label="Close")
    close.update_property([Gtk.AccessibleProperty.LABEL], [f"Close {v.get('title') or kind} panel"])
    close.connect("clicked", lambda *_: on_close(pid))
    head.append(close)
    box.append(head)
    text = v.get("text") or ""
    if kind == "markdown":
        body = v.get("body") or ""
        box.append(_label(body))
        if text and text.strip() != body.strip():
            box.append(_label(text, "panel-text"))
        return box
    if kind == "table":
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        for c, name in enumerate(v.get("columns") or []):
            grid.attach(_label(name, "panel-title"), c, 0, 1, 1)
        for r, row in enumerate(v.get("rows") or [], start=1):
            for c, cell in enumerate(row):
                grid.attach(_label(cell), c, r, 1, 1)
        box.append(grid)
    elif kind == "tasks":
        for title, status in v.get("tasks") or []:
            extra = {"running": " (working)", "failed": " (failed)"}.get(status, "")
            check = Gtk.CheckButton(label=title + extra, active=status == "done", can_target=False,
                                    can_focus=False)
            box.append(check)
    elif kind == "choice":
        if v.get("prompt"):
            box.append(_label(v["prompt"]))
        for oid, label in v.get("options") or []:
            b = Gtk.Button(label=label, css_classes=["big"], sensitive=v.get("chosen") is None)
            b.connect("clicked", lambda _b, o=oid: on_choice(pid, o))
            box.append(b)
    elif kind == "chart":
        bars = v.get("bars") or []
        top = max((val for _, val in bars), default=0.0)
        for name, val in bars:
            row = Gtk.Box(spacing=12)
            row.append(_label(name, wrap=False))
            bar = Gtk.ProgressBar(fraction=(val / top) if top > 0 else 0.0, hexpand=True, valign=Gtk.Align.CENTER)
            bar.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}: {val:g}"])
            row.append(bar)
            row.append(_label(f"{val:g}", wrap=False))
            box.append(row)
    elif kind in ("diff", "file"):
        parts = v.get("files") if kind == "diff" else [(v.get("path", ""), v.get("content", ""))]
        for path, content in parts or []:
            if path:
                box.append(_label(path, "mono"))
            view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["mono"])
            view.get_buffer().set_text(content)
            box.append(Gtk.ScrolledWindow(child=view, min_content_height=160, max_content_height=360,
                                          propagate_natural_height=True))
    elif kind == "approval":
        box.append(_label(v.get("summary") or ""))
    if text:
        box.append(_label(text, "panel-text"))
    return box


class Card(Gtk.Window):
    """The approval card: big plain words, Yes and No, answered by click or the Y and N keys."""

    def __init__(self, on_answer: Callable[..., None], on_defer: Callable[[], None],
                 on_review_lost: Callable[[], None]) -> None:
        super().__init__(title="Exo asks", css_classes=["exo", "exo-card"])
        self.layered = _layer(self, "OVERLAY", (), "NONE", "card")
        self.on_answer = on_answer
        self.on_defer = on_defer
        self.on_review_lost = on_review_lost
        self.shown_at = 0
        self.options: list[str] = []
        self.token: tuple[Any, ...] | None = None
        self.reviewing = False
        self._review_was_active = False
        self.connect("notify::is-active", self._activation_changed)
        self.pressed_keys: set[int] = set()  # persists across cards until a real release is observed
        self.review_keys: set[int] = set()  # F6 also needs a fresh physical press for each request
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin_top=32, margin_bottom=32,
                      margin_start=40, margin_end=40)
        self.summary = _label("", "card-summary")
        self.summary.set_max_width_chars(40)
        self.summary.set_selectable(True)  # safe first focus target; the request is read before the buttons
        self.review_hint = _label("Choose Review request (F6) to use Y/N keys.", "status")
        self.detail = Gtk.TextView(editable=False, cursor_visible=True, monospace=True,
                                   wrap_mode=Gtk.WrapMode.WORD_CHAR, css_classes=["mono"])
        self.detail.set_direction(Gtk.TextDirection.LTR)
        self.detail.update_property([Gtk.AccessibleProperty.LABEL], ["Full request details"])
        self.detail_scroll = Gtk.ScrolledWindow(child=self.detail, visible=False,
                                                min_content_height=120, max_content_height=360,
                                                propagate_natural_height=True)
        self.detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_default_size(720, -1)
        self.buttons = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER)
        for w in (self.summary, self.review_hint, self.detail_scroll, self.buttons):
            box.append(w)
        self.set_child(box)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        keys.connect("key-released", self._key_released)
        self.add_controller(keys)

    def show_card(self, summary: str, detail: str, options: list[str], token: tuple[Any, ...],
                  *, reviewing: bool = False) -> None:
        changed = token != self.token or reviewing != self.reviewing or not self.get_visible()
        self.token, self.reviewing = token, reviewing
        self.review_hint.set_visible(not reviewing)
        self.summary.set_text(summary)
        self.detail.get_buffer().set_text(detail)  # literal text; never model-controlled markup
        self.detail_scroll.set_visible(bool(detail))
        self.detail_scroll.get_vadjustment().set_value(0)
        self.options = list(options)
        _clear(self.buttons)
        for label, decision, cls in ((YES, "allow", ["big", "yes"]), (NO, "deny", ["big"]),
                                     (ALWAYS, "allow_always", ["big"])):
            if decision in options:
                b = Gtk.Button(label=label, css_classes=cls)
                b.connect("clicked", lambda _b, d=decision, t=token: self._answer(d, t, explicit_click=True))
                self.buttons.append(b)
        self.update_property([Gtk.AccessibleProperty.LABEL], [summary])
        # Every card shown gets a fresh arm window, even one that replaces another while visible:
        # a key still being typed must never land on the next question.
        if changed:
            self.shown_at = GLib.get_monotonic_time()
        _keyboard(self, "EXCLUSIVE" if reviewing else "NONE")
        if reviewing:
            if changed:
                self._review_was_active = self.is_active()
                self.present()
                self.summary.grab_focus()
        else:
            # Layer keyboard NONE is a compositor-enforced noninteractive preview.
            # Plain GTK windows have no equivalent nonactivation guarantee: the bar
            # displays their pending summary until the person explicitly reviews it.
            self.set_visible(self.layered)

    def _activation_changed(self, *_args: Any) -> None:
        if not self.reviewing:
            return
        if self.is_active():
            self._review_was_active = True
        elif self._review_was_active:
            self._review_was_active = False
            self.on_review_lost()

    def armed(self) -> bool:
        return (GLib.get_monotonic_time() - self.shown_at) / 1000 >= ARM_MS

    def _answer(self, decision: str, token: tuple[Any, ...] | None = None,
                *, explicit_click: bool = False) -> None:
        token = self.token if token is None else token
        if (token == self.token and token is not None and self.get_visible()
                and self.armed() and decision in self.options
                and (explicit_click or self.reviewing)):
            self.on_answer(decision, token, explicit_click=explicit_click)

    @staticmethod
    def _physical_key(keyval: int, code: int) -> int:
        return code or keyval  # synthetic tests may not supply a hardware code

    def _key_released(self, _ctl: Any, keyval: int, code: int, _state: Gdk.ModifierType) -> None:
        physical = self._physical_key(keyval, code)
        self.pressed_keys.discard(physical)
        self.review_keys.discard(physical)

    def fresh_review_key(self, keyval: int, code: int, state: Gdk.ModifierType) -> bool:
        """Track F6 across all windows; repeats or modifier changes never reopen review."""
        physical = self._physical_key(keyval, code)
        repeated = physical in self.review_keys
        self.review_keys.add(physical)
        blocked = (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK |
                   Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
        return not repeated and not (state and state & blocked)

    def observe_key_press(self, keyval: int, code: int) -> None:
        if approval_key(Gdk.keyval_name(keyval) or "") in ("allow", "deny"):
            self.pressed_keys.add(self._physical_key(keyval, code))

    def _key(self, _ctl: Any, keyval: int, code: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_F6:
            self.fresh_review_key(keyval, code, state)  # observe, never restart an active review
            return True
        if not self.reviewing:
            return False
        action = approval_key(Gdk.keyval_name(keyval) or "")
        physical = self._physical_key(keyval, code)
        if action == "defer":
            self.on_defer()
            return True
        if action:
            repeated = physical in self.pressed_keys
            self.pressed_keys.add(physical)
            blocked = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK |
                       Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK)
            if state and state & blocked:
                return False
            if not repeated:
                self._answer(action)
            return True
        return False


# -- the surface


class SurfaceUI:
    """The three windows, drawn from a SurfaceModel. Kept apart from Gtk.Application so it can be built
    and tested on any GTK display without registering on D-Bus."""

    def __init__(self, model: SurfaceModel, send: Callable[[Any], None],
                 launch: Callable[[str, Callable[[str], None]], None] = spawn_app,
                 app: Gtk.Application | None = None) -> None:
        self.model = model
        self.send = send
        self.launch = launch
        self._direction(model.profile)
        _install_css()
        self.bar, self.drawer, self.card = Bar(), Drawer(), Card(self._answer, self._defer, self._review_lost)
        for w in (self.bar, self.drawer, self.card):
            if app is not None:
                app.add_window(w)
            w.set_hide_on_close(True)
        self.bar.entry.connect("changed", lambda e: self.render(model.set_input(e.get_text())))
        self.bar.entry.connect("activate", lambda *_: self.submit())
        self.bar.review.connect("clicked", lambda *_: self.review_approval())
        self.bar.suggestions.connect("row-activated", self._row_activated)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._bar_key)
        keys.connect("key-released", self.card._key_released)
        self.bar.add_controller(keys)
        dkeys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        dkeys.connect("key-pressed", self._drawer_key)
        dkeys.connect("key-released", self.card._key_released)
        self.drawer.add_controller(dkeys)

    # -- showing and hiding

    def toggle(self) -> None:
        self.hide() if self.bar.get_visible() else self.show()

    def show(self, profile: Profile | None = None) -> None:
        self.model.leave_approval_review()
        self._close_drawer(restore_focus=False)  # an explicit prompt opening ends local drawer reading
        if profile is not None:
            self.model.set_profile(profile)
        self._direction(self.model.profile)
        self.bar.present()
        _keyboard(self.bar, "EXCLUSIVE")  # explicit prompt opening owns the keys
        self.render({"status", "transcript", "palette", "panels", "approval"})
        self._focus_prompt()

    def _direction(self, profile: Profile) -> None:
        direction = Gtk.TextDirection.RTL if profile.get("surface.language") in ("ar", "ur") else Gtk.TextDirection.LTR
        Gtk.Widget.set_default_direction(direction)
        for window in (getattr(self, "bar", None), getattr(self, "drawer", None), getattr(self, "card", None)):
            if window is not None:
                window.set_direction(direction)

    def _focus_prompt(self) -> None:
        """Return keys to the prompt when neither review nor an explicit drawer owns them."""
        if (self.bar.get_visible() and self.model.reviewed_approval is None
                and not self.drawer.interactive):
            self.bar.present()
            focused = self.bar.get_focus()
            while focused is not None:
                if focused == self.bar.entry:
                    return  # re-grabbing GtkText can select the text already being edited
                focused = focused.get_parent()
            self.bar.entry.grab_focus()

    def hide(self) -> None:
        self.model.leave_approval_review()
        self._close_drawer(restore_focus=False)
        self.bar.set_visible(False)
        self.bar.entry.set_text("")
        self.render(self.model.clear_transcript() | {"approval"})

    # -- drawing

    def render(self, changes: set[str]) -> None:
        m = self.model
        self.bar.status.set_text(m.status)
        self.bar.review.set_visible(bool(m.approvals) and m.reviewed_approval is None)
        self.bar.review.set_sensitive(m.connected)
        pending = m.approval
        self.bar.pending.set_text(pending.summary if pending is not None else "")
        self.bar.pending.set_visible(pending is not None and not self.card.layered
                                     and m.reviewed_approval is None)
        if "transcript" in changes:
            self.bar.show_transcript(m.visible_lines(), m.stream_text())
        if "palette" in changes:
            self.bar.show_suggestions(m.suggestions, m.selected)
        if "approval" in changes:
            was_reviewing = self.card.reviewing
            reviewed = m.reviewed_approval
            card = reviewed or m.approval
            if card is None:
                self.card.reviewing = False
                self.card.set_visible(False)
                if was_reviewing and not self.drawer.interactive:
                    _keyboard(self.bar, "EXCLUSIVE")
                    self._focus_prompt()
            else:
                if reviewed is not None and self.drawer.interactive:
                    self._close_drawer(restore_focus=False)
                if reviewed is not None:
                    _keyboard(self.bar, "ON_DEMAND")
                self.card.show_card(card.summary, card.detail, card.options, approval_token(card),
                                    reviewing=reviewed is not None)
                if reviewed is None and was_reviewing and not self.drawer.interactive:
                    _keyboard(self.bar, "EXCLUSIVE")
                    self._focus_prompt()
        if "panels" in changes:
            self._render_panels()
        if self.drawer.get_visible():  # the bar grows with the transcript; keep the drawer under it
            GLib.idle_add(lambda: self.drawer.below(self.bar.get_height()) or False)
        for body in m.take_outbox():
            self._send(body)

    def _render_panels(self) -> None:
        m = self.model
        if self.drawer.interactive and self.drawer.mode == "transcript":
            return  # a local reading view is a snapshot until the person closes it
        if not self.bar.get_visible():
            self.drawer.set_visible(False)
            return
        if not m.panels and not self.drawer.interactive:
            self.drawer.set_visible(False)
            return
        views = [{**panel_view(p), "panel_id": p.panel_id} for p in reversed(m.panels.values())]
        self.drawer.show_panels(views, self.close_panel, self.choose)
        self.drawer.below(self.bar.get_height())
        if not self.drawer.interactive:
            _keyboard(self.drawer, "NONE")
        # Model output only redraws/maps. Explicit _focus_drawer owns activation;
        # a background update must not select prompt text or reclaim another window's keys.
        self.drawer.set_visible(True)
        for p in list(m.panels.values()):
            if (body := m.panel_shown(p.panel_id)) is not None:
                self._send(body)

    def _send(self, body: Any) -> None:
        self.send(body)

    # -- actions

    def submit(self) -> None:
        act = self.model.submit()
        self.bar.entry.set_text("")
        if act.kind == "show_transcript":
            self.drawer.show_recent_conversation(self.model.recent_conversation(), self._close_drawer)
            self._focus_drawer()
            return
        if act.kind == "show_panels":
            self.drawer.interactive = True
            self.drawer.mode = "panels"
            self._render_panels()
            self._focus_drawer()
            return
        if act.kind == "open_app":
            self.launch(act.value, lambda name: self.render(self.model.app_failed(name)))
            self.hide()
            return
        if act.kind == "send" and act.command is not None:
            self._send(act.command)
        self.render({"transcript", "palette"})

    def _focus_drawer(self) -> None:
        self.drawer.interactive = True
        _keyboard(self.bar, "ON_DEMAND")
        _keyboard(self.drawer, "EXCLUSIVE")  # only after a person explicitly opens the drawer
        self.drawer.present()
        (self.drawer.transcript_view or self.drawer.box).grab_focus()

    def _close_drawer(self, restore_focus: bool = True) -> None:
        self.drawer.interactive = False
        _keyboard(self.drawer, "NONE")
        self.drawer.set_visible(False)
        self.drawer.mode = "panels"
        self.drawer.transcript_view = None
        if self.model.reviewed_approval is None:
            _keyboard(self.bar, "EXCLUSIVE")
            if restore_focus:
                self._focus_prompt()

    def close_panel(self, panel_id: str) -> None:
        if (body := self.model.close_panel(panel_id)) is not None:
            self._send(body)
        self.render({"panels"})

    def choose(self, panel_id: str, option_id: str) -> None:
        for body in self.model.choose(panel_id, option_id):
            self._send(body)
        self.render({"transcript", "panels"})

    def _answer(self, decision: str, token: tuple[Any, ...], *, explicit_click: bool = False) -> None:
        if (body := self.model.answer(decision, token, explicit_click=explicit_click)) is not None:
            self._send(body)
        self.render({"approval"})

    def review_approval(self) -> None:
        """A button or F6 in this surface, never ordinary typing, enters request review."""
        self.render(self.model.show_approvals())

    def _review_lost(self) -> None:
        """A previously active review lost focus; release it without activating another window."""
        self.model.leave_approval_review()
        self.card.reviewing = False
        _keyboard(self.card, "NONE")
        self.card.set_visible(False)
        self.render({"status"})  # no present(), focus restoration, or background remapping

    def _defer(self) -> None:
        self.render(self.model.defer_approval())
        self._focus_prompt()

    def _row_activated(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        self.model.selected = row.get_index()
        self.submit()

    def _bar_key(self, _ctl: Any, keyval: int, code: int, _state: Any) -> bool:
        self.card.observe_key_press(keyval, code)
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        if keyval == Gdk.KEY_F6:
            if self.card.fresh_review_key(keyval, code, _state):
                self.review_approval()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and self.model.suggestions:
            self.render(self.model.move(1 if keyval == Gdk.KEY_Down else -1))
            return True
        return False

    def _drawer_key(self, _ctl: Any, keyval: int, code: int, _state: Any) -> bool:
        self.card.observe_key_press(keyval, code)
        if keyval == Gdk.KEY_F6:
            if self.card.fresh_review_key(keyval, code, _state):
                self.review_approval()
            return True
        if keyval == Gdk.KEY_Escape:
            self._close_drawer()
            return True
        return False

    # -- the connection's listener (called on the GTK thread)

    def on_connected(self, up: bool) -> None:
        self.render(self.model.set_connected(up))

    def on_session(self, session: str) -> None:
        self.model.set_session(session)

    def on_approvals(self, rows: list[dict[str, Any]], through_seq: int) -> None:
        self.render(self.model.reconcile_approvals(rows, through_seq))

    def on_approval_result(self, request_id: str) -> None:
        self.render(self.model.approval_result(request_id))

    def on_model_spend(self, value: Any) -> None:
        self.render(self.model.model_spend(value))

    def on_event(self, ev: s.Event) -> None:
        self.render(self.model.apply(ev))

    def on_failed(self, code: str, message: str) -> None:
        self.render(self.model.command_failed(code, message))


_css_done = False


def _install_css() -> None:
    global _css_done
    if _css_done:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_done = True

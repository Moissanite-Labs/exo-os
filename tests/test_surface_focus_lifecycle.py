"""Exercise UI lifecycle methods without a display; native focus stays in the GTK probe.

The doubles supply only the widget API these methods consume. They do not invent
compositor activation or keyboard delivery.
"""
import importlib.util
import sys
from enum import IntFlag
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from exo_os.profile import Profile
from exo_os.surface.model import ApprovalCard, Panel, SurfaceModel


class Window:
    def get_visible(self):
        return self.visible

    def set_visible(self, visible):
        self.visible = visible

    def update_property(self, *_args):
        pass


class Modifiers(IntFlag):
    SHIFT_MASK = 1
    CONTROL_MASK = 2
    ALT_MASK = 4
    SUPER_MASK = 8
    META_MASK = 16


@pytest.fixture
def ui_module(monkeypatch):
    gi = ModuleType("gi")

    def require_version(name, _version):
        if name == "Gtk4LayerShell":
            raise ValueError("host doubles have no layer shell")

    gi.require_version = require_version
    repository = ModuleType("gi.repository")
    repository.Gtk = SimpleNamespace(Window=Window, Button=lambda **_kw: MagicMock(),
                                    AccessibleProperty=SimpleNamespace(LABEL="label"))
    repository.Gdk = SimpleNamespace(ModifierType=Modifiers, KEY_F6=77, KEY_y=121, KEY_n=110,
                                    keyval_name=lambda value: {121: "y", 110: "n", 77: "F6"}.get(value, ""))
    repository.GLib = SimpleNamespace(get_monotonic_time=lambda: 10_000_000,
                                     idle_add=lambda _callback: None)
    repository.Pango = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    path = Path(__file__).resolve().parents[1] / "src/exo_os/surface/ui.py"
    # A separate name avoids replacing a real GTK module in a display-enabled run.
    spec = importlib.util.spec_from_file_location("exo_os.surface._focus_lifecycle_ui", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_card(ui):
    card = ui.Card.__new__(ui.Card)
    card.token, card.reviewing, card.layered = ("request",), False, True
    card.visible, card.shown_at = False, 8_000_000
    card.summary, card.review_hint, card.detail, card.detail_scroll = [MagicMock() for _ in range(4)]
    card.buttons = MagicMock()
    card.buttons.get_first_child.return_value = None
    card.pressed_keys, card.review_keys = {29}, {77}
    card.on_answer = MagicMock()
    card.on_defer = MagicMock()
    card.is_active = lambda: False
    card.present = lambda: card.set_visible(True)
    return card


@pytest.mark.parametrize("keyval,code,decision", [(121, 29, "allow"), (110, 30, "deny")])
def test_hidden_same_request_preview_gets_fresh_arm_without_releasing_physical_keys(ui_module, keyval, code, decision):
    ui = ui_module
    card = make_card(ui)
    card.pressed_keys = {code}
    token = card.token
    card.show_card("Same request", "{}", ["allow", "deny"], token)
    assert card.get_visible() and not card.armed()
    card._answer("allow", token, explicit_click=True)
    card.on_answer.assert_not_called()
    assert card.pressed_keys == {code} and card.review_keys == {77}

    # Explicit review also arms freshly. A held Y or F6 remains physically held
    # through both remaps and cannot become a new consent gesture.
    card.show_card("Same request", "{}", ["allow", "deny"], token, reviewing=True)
    ui.GLib.get_monotonic_time = lambda: card.shown_at + (ui.ARM_MS + 1) * 1000
    card._key(None, keyval, code, 0)
    assert not card.fresh_review_key(ui.Gdk.KEY_F6, 77, 0)
    card.on_answer.assert_not_called()
    card._key_released(None, keyval, code, 0)
    card._key(None, keyval, code, 0)
    card.on_answer.assert_called_once_with(decision, token, explicit_click=False)
    card._key_released(None, ui.Gdk.KEY_F6, 77, 0)
    assert card.fresh_review_key(ui.Gdk.KEY_F6, 77, 0)


def test_visible_same_request_refresh_does_not_restart_review_arm(ui_module):
    card = make_card(ui_module)
    card.visible = True
    shown_at = card.shown_at
    card.show_card("Same request", "{}", ["allow", "deny"], card.token)
    assert card.shown_at == shown_at


def make_surface(ui, *, interactive, pending):
    surface = ui.SurfaceUI.__new__(ui.SurfaceUI)
    surface.model = SimpleNamespace(status="Ready", approvals=[pending] if pending else [], connected=True,
                                    approval=pending, reviewed_approval=None, take_outbox=lambda: [])
    surface.bar = MagicMock()
    surface.bar.get_visible.return_value = True
    surface.bar.get_focus.return_value = None
    surface.drawer = MagicMock()
    surface.drawer.interactive = interactive
    surface.drawer.get_visible.return_value = True
    surface.card = MagicMock()
    surface.card.layered = True
    surface.card.reviewing = True
    surface.card.show_card.side_effect = lambda *_args, reviewing: setattr(surface.card, "reviewing", reviewing)
    surface.send = MagicMock()
    return surface


@pytest.mark.parametrize("pending", [None, ApprovalCard("next", "Next request", binding="binding", detail="{}")])
@pytest.mark.parametrize("interactive", [False, True])
def test_reconciliation_restores_prompt_only_when_drawer_does_not_own_keys(ui_module, pending, interactive):
    ui = ui_module
    surface = make_surface(ui, interactive=interactive, pending=pending)
    modes = []
    ui._keyboard = lambda window, mode: modes.append((window, mode))
    surface.render({"approval"})
    assert not surface.card.reviewing
    if interactive:
        assert modes == []
        surface.bar.present.assert_not_called()
        surface.bar.entry.grab_focus.assert_not_called()
    else:
        assert modes == [(surface.bar, "EXCLUSIVE")]
        surface.bar.present.assert_called_once()
        surface.bar.entry.grab_focus.assert_called_once()
    surface.send.assert_not_called()  # neither ending review nor showing a preview answers a request


def test_prompt_focus_request_does_not_displace_explicit_drawer(ui_module):
    surface = make_surface(ui_module, interactive=True, pending=None)
    surface._focus_prompt()
    surface.bar.present.assert_not_called()
    surface.bar.entry.grab_focus.assert_not_called()


def test_explicit_prompt_opening_releases_interactive_drawer_without_answering(ui_module):
    ui = ui_module
    surface = make_surface(ui, interactive=True, pending=None)
    surface.model = SurfaceModel(Profile.default())
    surface.model.set_connected(True)
    pending = ApprovalCard("pending", "Still requires explicit review", binding="bound")
    surface.model.approvals.append(pending)
    surface._direction = MagicMock()  # language rendering does not affect keyboard ownership
    surface.drawer.mode = "transcript"
    surface.drawer.transcript_view = MagicMock()
    modes = {surface.bar: "ON_DEMAND", surface.drawer: "EXCLUSIVE", surface.card: "NONE"}
    ui._keyboard = lambda window, mode: modes.__setitem__(window, mode)
    surface.show()
    assert modes == {surface.bar: "EXCLUSIVE", surface.drawer: "NONE", surface.card: "NONE"}
    assert not surface.drawer.interactive and surface.drawer.mode == "panels"
    assert surface.drawer.transcript_view is None
    surface.drawer.set_visible.assert_called_with(False)
    surface.bar.present.assert_called()
    surface.bar.entry.grab_focus.assert_called()
    surface.send.assert_not_called()
    assert surface.model.reviewed_approval is None
    assert surface.model.approval is pending and not surface.card.reviewing


@pytest.mark.parametrize("nested", [False, True])
def test_prompt_restoration_keeps_existing_entry_target_without_selecting_text(ui_module, nested):
    surface = make_surface(ui_module, interactive=False, pending=None)
    focused = SimpleNamespace(get_parent=lambda: surface.bar.entry) if nested else surface.bar.entry
    surface.bar.get_focus.return_value = focused
    surface._focus_prompt()
    surface.bar.present.assert_called_once()  # restoring/opening the prompt is still intentional
    surface.bar.entry.grab_focus.assert_not_called()


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("owner", ["prompt", "other-window"])
def test_background_panel_refresh_never_requests_activation_or_changes_editor(ui_module, interactive, owner):
    ui = ui_module
    surface = make_surface(ui, interactive=interactive, pending=None)
    surface.model = SurfaceModel(Profile.default())
    surface.model.panels["chart"] = Panel("chart", kind="chart", title="Chart", data={"labels": ["Mon"],
                                                  "values": [5]}, shown=True)
    surface.drawer.mode = "panels"
    # This data is inert. We assert that real UI methods do not request any
    # operation that can alter it; the double does not simulate compositor focus.
    editor = {"text": "exo ", "cursor": 4, "selection": (1, 3), "owner": owner}
    before = dict(editor)
    surface.bar.entry.get_text.side_effect = lambda: editor["text"]
    surface.bar.entry.get_position.side_effect = lambda: editor["cursor"]
    surface.bar.entry.get_selection_bounds.side_effect = lambda: editor["selection"]
    surface.bar.get_focus.return_value = SimpleNamespace(get_parent=lambda: surface.bar.entry)
    modes = []
    ui._keyboard = lambda window, mode: modes.append((window, mode))
    surface._focus_prompt = MagicMock(wraps=surface._focus_prompt)
    surface.render({"panels"})
    assert editor == before
    surface.drawer.show_panels.assert_called_once()
    surface.drawer.set_visible.assert_called_once_with(True)
    surface.drawer.present.assert_not_called()
    surface.drawer.box.grab_focus.assert_not_called()
    surface._focus_prompt.assert_not_called()
    surface.bar.present.assert_not_called()
    surface.bar.entry.grab_focus.assert_not_called()
    surface.bar.entry.set_text.assert_not_called()
    surface.bar.entry.set_position.assert_not_called()
    surface.bar.entry.select_region.assert_not_called()
    assert modes == ([] if interactive else [(surface.drawer, "NONE")])
    surface.send.assert_not_called()  # an update to an already shown panel has no response


def test_explicit_drawer_opening_requests_ownership_and_target(ui_module):
    ui = ui_module
    surface = make_surface(ui, interactive=False, pending=None)
    surface.drawer.transcript_view = None
    modes = []
    ui._keyboard = lambda window, mode: modes.append((window, mode))
    surface._focus_drawer()
    assert surface.drawer.interactive
    assert modes == [(surface.bar, "ON_DEMAND"), (surface.drawer, "EXCLUSIVE")]
    surface.drawer.present.assert_called_once()
    surface.drawer.box.grab_focus.assert_called_once()
    surface.send.assert_not_called()

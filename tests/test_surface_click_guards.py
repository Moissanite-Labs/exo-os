"""Exercise real UI/model consent guards without claiming GTK navigation coverage."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from exo_os.profile import Profile
from exo_os.surface.model import ApprovalCard, SurfaceModel, approval_token


@pytest.fixture
def ui_module(monkeypatch):
    gi = ModuleType("gi")

    def require_version(name, _version):
        if name == "Gtk4LayerShell":
            raise ValueError("no host layer-shell")

    gi.require_version = require_version
    repository = ModuleType("gi.repository")
    repository.Gtk = SimpleNamespace(Window=object)
    repository.Gdk = SimpleNamespace()
    repository.GLib = SimpleNamespace(get_monotonic_time=lambda: 10_000_000)
    repository.Pango = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)
    path = Path(__file__).resolve().parents[1] / "src/exo_os/surface/ui.py"
    spec = importlib.util.spec_from_file_location("exo_os.surface._click_guard_ui", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pending_surface(ui):
    surface = ui.SurfaceUI.__new__(ui.SurfaceUI)
    surface.model = SurfaceModel(Profile.default())
    surface.model.set_connected(True)
    surface.model.approvals.append(ApprovalCard("pending", "Exact request", binding="bound"))
    surface.send = MagicMock()
    surface.render = MagicMock()  # these checks cover decision flow, not GTK rendering
    card = ui.Card.__new__(ui.Card)
    card.token = approval_token(surface.model.approval)
    card.shown_at = 0
    card.options = ["allow", "deny"]
    card.get_visible = lambda: True
    card.reviewing = False
    card.pressed_keys, card.review_keys = {29}, {77}
    card.on_answer = surface._answer
    return surface, card


@pytest.mark.parametrize("refresh_ui", [False, True])
@pytest.mark.parametrize("replacement", [False, True])
def test_queued_old_click_cannot_answer_changed_or_replaced_pending_request(ui_module, refresh_ui, replacement):
    surface, card = pending_surface(ui_module)
    captured = card.token
    if replacement:
        surface.model.approvals[0] = ApprovalCard("replacement", "New request", binding="new")
    else:
        surface.model.approvals[0].summary = "Changed request"
    pending = surface.model.approval
    if refresh_ui:
        card.token = approval_token(pending)
    card._answer("allow", captured, explicit_click=True)
    surface.send.assert_not_called()
    assert surface.model.approval is pending and surface.model.reviewed_approval is None
    assert card.pressed_keys == {29} and card.review_keys == {77}


def test_untargeted_ui_click_cannot_answer_current_pending_request(ui_module):
    surface, _card = pending_surface(ui_module)
    pending = surface.model.approval
    surface._answer("allow", None, explicit_click=True)
    surface.send.assert_not_called()
    assert surface.model.approval is pending


def test_targeted_current_preview_click_still_answers_without_keyboard_review(ui_module):
    surface, card = pending_surface(ui_module)
    card._answer("allow", card.token, explicit_click=True)
    surface.send.assert_called_once()
    assert surface.send.call_args.args[0].request_id == "pending"
    assert surface.model.approval is None and surface.model.reviewed_approval is None

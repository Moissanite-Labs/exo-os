"""Actual lifecycle methods under explicit widget states, not native focus proof."""
import sys

import pytest

import phase3_guard_mutations as guards


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(guards, 'ROOT', tmp_path)
    previous = {name: sys.modules.get(name) for name in ('gi', 'gi.repository')}
    yield
    for name, value in previous.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


def exercise(source, *, initially_mapped):
    ui, clock = guards.load_ui(source, 'mapped_review_' + str(initially_mapped))
    surface, sent = guards.make_surface(ui)
    state = {'mapped': initially_mapped, 'active': True}
    card = surface.card
    card.get_mapped = lambda: state['mapped']
    card.is_active = lambda: state['active']
    surface._bar_key(None, ui.Gdk.KEY_F6, 77, 0)
    assert surface.model.reviewed_approval.request_id == 'held_f6_a'
    shown_at = card.shown_at
    # This getter transition mirrors the observed notification; the host test
    # does not claim to reproduce GTK's event delivery or scheduling.
    state['active'] = False
    card._activation_changed()
    return ui, surface, sent, state, shown_at


def test_hidden_stale_active_flag_does_not_revoke_before_first_actual_activation(fixture):
    ui, surface, sent, state, shown_at = exercise(guards.SOURCE.read_text(), initially_mapped=False)
    card = surface.card
    assert surface.model.reviewed_approval is not None
    assert surface.model.reviewed_approval.request_id == 'held_f6_a'
    assert card.reviewing and not card._review_was_active and card.review_keys == {77} and not sent
    assert card.shown_at == shown_at
    state.update(mapped=True, active=True)
    card._activation_changed()
    assert card._review_was_active
    state['active'] = False
    card._activation_changed()
    assert surface.model.reviewed_approval is None and not card.reviewing and card.review_keys == {77}
    card._key(None, ui.Gdk.KEY_y, 78, 0)
    surface._drawer_key(None, ui.Gdk.KEY_F6, 77, 0)
    assert surface.model.reviewed_approval is None and not sent and card.shown_at == shown_at


def test_mapped_active_review_still_revokes_on_loss(fixture):
    _, surface, sent, _, shown_at = exercise(guards.SOURCE.read_text(), initially_mapped=True)
    assert surface.model.reviewed_approval is None and not surface.card.reviewing
    assert surface.card.review_keys == {77} and surface.card.shown_at == shown_at and not sent


def test_original_initializer_reproduces_stale_unmapped_activation(fixture):
    source = guards.SOURCE.read_text()
    before = 'self._review_was_active = self.get_mapped() and self.is_active()'
    assert source.count(before) == 1
    source = source.replace(before, 'self._review_was_active = self.is_active()')
    _, surface, sent, _, _ = exercise(source, initially_mapped=False)
    assert surface.model.reviewed_approval is None and not surface.card.reviewing and not sent

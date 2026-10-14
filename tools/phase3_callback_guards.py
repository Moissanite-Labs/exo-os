"""Explicitly synthetic callbacks on actual Card/SurfaceUI methods, on every backend.

These checks are independent of native delivery. Their private clock and getter
substitution are confined to this block and never enter native evidence arrays.
"""
GUARD_CASES = ('repeat-remap', 'modified-hold', 'held-answer', 'activation-loss')
MODIFIER_NAMES = ('SHIFT_MASK', 'CONTROL_MASK', 'ALT_MASK', 'SUPER_MASK', 'META_MASK')


def callback_guards(ui, A, s, ev, sent):
    card, model = ui.card, ui.model
    original_clock = A.GLib.get_monotonic_time
    now = [original_clock()]
    # Virtual time is only for adversarial callbacks, never native ARM proof.
    A.GLib.get_monotonic_time = lambda: now[0]
    result = {'mode': 'synthetic-actual-methods', 'cases': [], 'modifier_masks': list(MODIFIER_NAMES)}

    def arm():
        now[0] += (A.ARM_MS + 1) * 1000

    def request(rid):
        ui.on_event(ev(s.ApprovalRequested(request_id=rid, summary=rid, binding=rid)))

    def resolve(rid):
        ui.on_event(ev(s.ApprovalResolved(request_id=rid, decision='deny')))

    def release(key, code):
        card._key_released(None, key, code, 0)

    try:
        ui.show()
        request('guard_a'); request('guard_b')
        before = len(sent)
        ui._bar_key(None, A.Gdk.KEY_F6, 9001, 0)
        assert model.reviewed_approval.request_id == 'guard_a'
        initial = card.shown_at
        arm()
        card._key(None, A.Gdk.KEY_F6, 9001, 0)
        assert card.shown_at == initial
        card._key(None, A.Gdk.KEY_y, 9002, 0)
        release(A.Gdk.KEY_y, 9002)
        expected_a = s.ApprovalRespond(request_id='guard_a', decision='allow', client='exo-surface', binding='guard_a')
        assert sent[before:] == [expected_a]
        assert model.reviewed_approval is None and model.approval.request_id == 'guard_b'
        # Remapping the preview and both callback entry points preserve the hold.
        card.set_visible(False)
        ui.render({'approval'})
        for handler in (ui._bar_key, ui._drawer_key):
            handler(None, A.Gdk.KEY_F6, 9001, 0)
            assert model.reviewed_approval is None and sent[before:] == [expected_a]
        assert 9001 in card.review_keys
        release(A.Gdk.KEY_F6, 9001)
        resolve('guard_a'); resolve('guard_b')
        result['cases'].append('repeat-remap')

        request('guard_mod')
        before = len(sent)
        for name in MODIFIER_NAMES:
            ui._bar_key(None, A.Gdk.KEY_F6, 9003, getattr(A.Gdk.ModifierType, name))
            ui._drawer_key(None, A.Gdk.KEY_F6, 9003, 0)
            card._key(None, A.Gdk.KEY_F6, 9003, 0)
            assert model.reviewed_approval is None and len(sent) == before and 9003 in card.review_keys
            release(A.Gdk.KEY_F6, 9003)
        resolve('guard_mod')
        result['cases'].append('modified-hold')

        request('guard_y_a'); request('guard_y_b')
        before = len(sent)
        ui._bar_key(None, A.Gdk.KEY_F6, 9004, 0)
        arm()
        card._key(None, A.Gdk.KEY_y, 9005, 0)
        release(A.Gdk.KEY_F6, 9004)
        ui._drawer_key(None, A.Gdk.KEY_F6, 9004, 0)
        arm()
        card._key(None, A.Gdk.KEY_y, 9005, 0)
        assert len(sent) == before + 1 and model.reviewed_approval.request_id == 'guard_y_b'
        release(A.Gdk.KEY_y, 9005)
        release(A.Gdk.KEY_F6, 9004)
        resolve('guard_y_a'); resolve('guard_y_b')
        result['cases'].append('held-answer')

        request('guard_focus')
        before = len(sent)
        ui._bar_key(None, A.Gdk.KEY_F6, 9006, 0)
        # Explicit callback simulation, not evidence of compositor activation.
        original_active = card.is_active
        try:
            card.is_active = lambda: False
            card._review_was_active = True
            card._activation_changed()
        finally:
            card.is_active = original_active
        arm()
        card._key(None, A.Gdk.KEY_y, 9007, 0)
        ui._drawer_key(None, A.Gdk.KEY_F6, 9006, 0)
        assert model.reviewed_approval is None and len(sent) == before and 9006 in card.review_keys
        release(A.Gdk.KEY_y, 9007); release(A.Gdk.KEY_F6, 9006)
        resolve('guard_focus')
        result['cases'].append('activation-loss')
        assert tuple(result['cases']) == GUARD_CASES
        return result
    finally:
        A.GLib.get_monotonic_time = original_clock

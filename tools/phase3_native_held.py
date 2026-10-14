"""Unexecuted native prototype, separate from the host adversarial callbacks.

Integrate only after a ruling. Uses the probe's authenticated native wtype
helpers and original UI handlers. It never claims a duplicate press was delivered.
"""


import json
import subprocess
import sys

from phase3_stdin import ready_for_y


def native_held_transition(ui, A, s, sent, start_keys, finish_keys, wait_until, review_with_f6, finish_input):
    assert ui.model.approval.request_id == 'held_f6_a'
    n = len(sent)
    fresh_rows, typed_rows, release_rows, overflow = [], [], [], []
    arm_evidence = None
    original_fresh = ui.card.fresh_review_key
    blocked_mask = (A.Gdk.ModifierType.SHIFT_MASK | A.Gdk.ModifierType.CONTROL_MASK |
                    A.Gdk.ModifierType.ALT_MASK | A.Gdk.ModifierType.SUPER_MASK |
                    A.Gdk.ModifierType.META_MASK)

    def observe_fresh(keyval, code, state):
        pending, reviewed = ui.model.approval, ui.model.reviewed_approval
        row = {'pending_id': pending.request_id if pending else None,
               'reviewed_id': reviewed.request_id if reviewed else None,
               'code': code, 'held_before': code in ui.card.review_keys,
               'blocked': bool(state and state & blocked_mask)}
        if len(fresh_rows) < 128:
            fresh_rows.append(row)
        else:
            overflow[:] = [True]
        row['accepted'] = original_fresh(keyval, code, state)
        return row['accepted']

    def held_code():
        return next((row['code'] for row in fresh_rows if row['pending_id'] == 'held_f6_a'
                     and row['accepted'] and row['code'] > 0), None)

    def observe_text(_controller, keyval, code, _state):
        pending = ui.model.approval
        if pending is not None and pending.request_id == 'held_f6_b' and keyval != A.Gdk.KEY_F6:
            if len(typed_rows) >= 32:
                overflow[:] = [True]
                return False
            typed_rows.append({'keyval': keyval, 'code': code,
                               'f6_held': held_code() in ui.card.review_keys,
                               'reviewed': ui.model.reviewed_approval is not None})
        return False  # original GTK handler and entry insertion remain responsible

    def observe_release(_controller, keyval, code, _state):
        if keyval == A.Gdk.KEY_F6:
            if len(release_rows) < 32:
                release_rows.append({'code': code, 'keyval': keyval})
            else:
                overflow[:] = [True]

    controller = A.Gtk.EventControllerKey(propagation_phase=A.Gtk.PropagationPhase.CAPTURE)
    controller.connect('key-pressed', observe_text)
    controller.connect('key-released', observe_release)
    ui.bar.add_controller(controller)
    ui.card.fresh_review_key = observe_fresh
    proc = None
    try:
        # One keyboard retains F6 across A's answer, B's preview and all typing.
        # A second -P F6 would be another pressed-state request, not autorepeat.
        proc = start_keys('-P', 'F6', '-', '-s', '800', '-d', '150', 'yesterday', '-p', 'F6',
                          stdin=subprocess.PIPE)
        wait_until('native A review active and actually armed', lambda: ready_for_y(ui))
        arm_evidence = {'request_id': ui.model.reviewed_approval.request_id,
                        'active': ui.card.is_active(), 'armed': ui.card.armed()}
        assert not overflow
        # One literal y plus EOF resumes the SAME virtual keyboard after ARM.
        # The first communicate supplies y; timeout retries must use input=None.
        finish_input(proc, 'y')
        code = held_code()
        assert code is not None
        wait_until('native F6 release clears latch', lambda: code not in ui.card.review_keys)
        assert any(row['code'] == code for row in release_rows), release_rows
        expected_a = s.ApprovalRespond(request_id='held_f6_a', decision='allow', client='exo-surface', binding='fa')
        assert sent[n:] == [expected_a]
        assert ui.model.reviewed_approval is None and ui.model.approval.request_id == 'held_f6_b'
        assert ui.bar.entry.get_text() == ui.model.text == 'yesterday'
        assert len(typed_rows) == len('yesterday')
        assert all(row['keyval'] == ord(char) and row['code'] > 0 and row['f6_held']
                   and not row['reviewed'] for row, char in zip(typed_rows, 'yesterday'))
        repeats_after_b = [row for row in fresh_rows if row['pending_id'] == 'held_f6_b' and row['held_before']]
        assert all(not row['accepted'] for row in repeats_after_b)  # enforced if actual repeat happens
        before_fresh_b = {'a_allowed': sum(body == expected_a for body in sent[n:]),
                          'b_responses': sum(isinstance(body, s.ApprovalRespond) and body.request_id == 'held_f6_b'
                                             for body in sent[n:]),
                          'reviewed': ui.model.reviewed_approval is not None,
                          'prompt_text': ui.bar.entry.get_text(), 'model_text': ui.model.text}
        released = code not in ui.card.review_keys
        cut = len(fresh_rows)
        review_with_f6()  # actual native helper after observed release, never a direct callback here
        wait_until('fresh B review active', lambda: ui.model.reviewed_approval is not None
                   and ui.model.reviewed_approval.request_id == 'held_f6_b' and ui.card.is_active())
        assert any(row['pending_id'] == 'held_f6_b' and row['code'] > 0 and not row['held_before']
                   and not row['blocked'] and row['accepted'] for row in fresh_rows[cut:])
        wait_until('fresh B review ARM elapsed', ui.card.armed)
        fresh_b_armed = ui.card.is_active() and ui.card.armed()
        finish_keys(start_keys('-k', 'n'))
        expected_b = s.ApprovalRespond(request_id='held_f6_b', decision='deny', client='exo-surface', binding='fb')
        assert sent[n:] == [expected_a, expected_b]
        assert not overflow
        return {'mode': 'native-held-transition', 'events': fresh_rows, 'typed_events': typed_rows,
                'release_events': release_rows, 'before_fresh_b': before_fresh_b,
                'fresh_b_start_index': cut,
                'repeat_after_b_delivered': bool(repeats_after_b),
                'fresh_b_denied': sent[n:] == [expected_a, expected_b],
                'arm': arm_evidence, 'released': released,
                'overflow': bool(overflow), 'responses': [
                    {'request_id': body.request_id, 'decision': body.decision, 'client': body.client, 'binding': body.binding}
                    for body in sent[n:]], 'fresh_b_armed': fresh_b_armed}
    except BaseException as exc:
        failure_snapshot(ui, exc, fresh_rows, typed_rows, release_rows, bool(overflow))
        raise
    finally:
        ui.card.fresh_review_key = original_fresh
        ui.bar.remove_controller(controller)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def failure_snapshot(ui, exc, events, typed, releases, overflow):
    """Capture bounded evidence before cleanup; never emit partial success."""
    def bounded_rows(rows):
        return rows if len(rows) <= 32 else rows[:16] + rows[-16:]

    record = {'probe': 'exo-surface-gtk-v3', 'status': 'FAIL', 'diagnostic': 'native-held-transition',
              'error': type(exc).__name__, 'overflow': overflow,
              'event_counts': [len(events), len(typed), len(releases)],
              'events': bounded_rows(events), 'typed_events': bounded_rows(typed),
              'release_events': bounded_rows(releases)}
    try:
        record.update({'entry_text': ui.bar.entry.get_text()[:2000], 'model_text': ui.model.text[:2000],
                       'review_keys': sorted(ui.card.review_keys)[:32],
                       'reviewed_id': ui.model.reviewed_approval.request_id if ui.model.reviewed_approval else None})
    except Exception as getter_error:
        record['state_error'] = type(getter_error).__name__
    payload = json.dumps(record, sort_keys=True)
    if len(payload.encode()) > 16384:
        payload = json.dumps({'probe': 'exo-surface-gtk-v3', 'status': 'FAIL',
                              'diagnostic': 'native-held-transition-snapshot-oversized',
                              'error': type(exc).__name__, 'overflow': overflow,
                              'event_counts': record['event_counts']})
    print(payload, file=sys.stderr, flush=True)

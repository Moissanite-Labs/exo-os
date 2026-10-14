"""Strict independent record predicates; all records here are fabricated fixtures."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from phase3_input_evidence import native_held_transition_delivered, adversarial_guards_checked  # noqa: E402
from phase3_records import held_record, guard_record


def test_native_transition_without_repeat_is_distinct_from_synthetic_guards():
    assert native_held_transition_delivered(held_record())
    assert adversarial_guards_checked(guard_record())
    assert not native_held_transition_delivered(guard_record())
    assert not adversarial_guards_checked(held_record())


def test_optional_actual_repeat_must_reject():
    record = held_record()
    record['events'].insert(1, {'pending_id': 'held_f6_b', 'reviewed_id': None, 'code': 9,
                              'held_before': True, 'blocked': False, 'accepted': False})
    record['fresh_b_start_index'] = 2
    record['repeat_after_b_delivered'] = True
    assert native_held_transition_delivered(record)
    record['events'][1]['accepted'] = True
    assert not native_held_transition_delivered(record)


@pytest.mark.parametrize('field,value', [('mode', 'native'), ('released', False), ('released', 1), ('overflow', True),
    ('fresh_b_start_index', True), ('fresh_b_start_index', 0), ('repeat_after_b_delivered', True),
    ('fresh_b_denied', False), ('fresh_b_armed', False), ('responses', []), ('typed_events', []), ('events', []),
    ('release_events', []), ('arm', {'request_id': 'held_f6_a', 'active': 1, 'armed': True}), ('extra', True)])
def test_incomplete_or_mislabelled_native_transition_rejected(field, value):
    record = held_record(); record[field] = value
    assert not native_held_transition_delivered(record)


@pytest.mark.parametrize('path,value', [(('before_fresh_b', 'a_allowed'), True), (('before_fresh_b', 'b_responses'), 1),
    (('before_fresh_b', 'reviewed'), True), (('before_fresh_b', 'prompt_text'), 'yest'),
    (('before_fresh_b', 'model_text'), 'yest'), (('arm', 'armed'), False), (('arm', 'active'), False),
    (('arm', 'request_id'), 'held_f6_b')])
def test_ordered_native_checkpoint_requirements(path, value):
    record = held_record(); record[path[0]][path[1]] = value
    assert not native_held_transition_delivered(record)


@pytest.mark.parametrize('array,field,value', [('typed_events', 'code', 0), ('typed_events', 'code', True),
    ('typed_events', 'keyval', 0), ('typed_events', 'f6_held', False), ('typed_events', 'reviewed', True),
    ('events', 'code', 0), ('events', 'held_before', True), ('events', 'accepted', False),
    ('release_events', 'code', 8), ('release_events', 'keyval', 121)])
def test_received_native_key_and_matching_release_requirements(array, field, value):
    record = held_record(); record[array][0][field] = value
    assert not native_held_transition_delivered(record)


def test_arrays_are_bounded_and_synthetic_modifier_claims_complete():
    record = held_record(); record['release_events'] *= 33
    assert not native_held_transition_delivered(record)
    for field in ('cases', 'modifier_masks'):
        record = copy.deepcopy(guard_record()); record[field].pop()
        assert not adversarial_guards_checked(record)


def test_failure_snapshot_is_bounded_fail_even_when_state_getter_breaks(capsys):
    import json
    from types import SimpleNamespace
    from phase3_native_held import failure_snapshot
    def broken():
        raise RuntimeError('getter failed')
    ui = SimpleNamespace(bar=SimpleNamespace(entry=SimpleNamespace(get_text=broken)))
    events = held_record()['events'] * 64
    failure_snapshot(ui, AssertionError(), events, [], [], True)
    payload = capsys.readouterr().err
    assert len(payload.encode()) <= 16384
    row = json.loads(payload)
    assert row['status'] == 'FAIL' and row['overflow'] is True and row['event_counts'][0] == 128
    assert len(row['events']) == 32 and row['state_error'] == 'RuntimeError'

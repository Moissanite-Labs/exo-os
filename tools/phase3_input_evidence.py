"""Coverage criteria for native keyboard regressions, independent of GTK imports."""
from __future__ import annotations

PROMPT_MARKER = "exo focus marker"
PROMPT_STAGES = ("unsolicited-panels", "panels-close", "updated-panels-close", "unsolicited-panel-update",
                 "approval-defer", "external-work-return", "generic-answer-return")


def native_prompt_delivered(record: object) -> bool:
    """A local focus target alone cannot prove the compositor delivered keys."""
    keys = {"stage", "text", "model_text", "events", "sent_delta", "review_unchanged", "reviewed", "entry_target",
            "mapped", "gtk_active", "panel_update_injected"}
    if (not isinstance(record, dict) or set(record) != keys or record["stage"] not in PROMPT_STAGES
            or record["text"] != PROMPT_MARKER or record["model_text"] != PROMPT_MARKER
            or type(record["sent_delta"]) is not int or record["sent_delta"] != 0
            or record["reviewed"] is not False
            or any(record[key] is not True for key in ("review_unchanged", "entry_target", "mapped"))
            or type(record["gtk_active"]) is not bool
            or record["panel_update_injected"] is not (record["stage"] == "unsolicited-panel-update")):
        return False
    events = record["events"]
    return (isinstance(events, list) and len(events) == len(PROMPT_MARKER)
            and all(isinstance(row, dict) and set(row) == {"keyval", "code"}
                    and type(row["keyval"]) is int and row["keyval"] == ord(char)
                    and type(row["code"]) is int and row["code"] > 0
                    for row, char in zip(events, PROMPT_MARKER)))


def native_external_panel_delivered(record: object) -> bool:
    """A panel update must leave actual typing in the other work window."""
    keys = {"mode", "text", "events", "panel_update_injected", "sent_delta", "reviewed",
            "external_active", "external_target", "prompt_unchanged"}
    if (not isinstance(record, dict) or set(record) != keys or record["mode"] != "native"
            or record["text"] != "yesterday" or record["reviewed"] is not False
            or type(record["sent_delta"]) is not int or record["sent_delta"] != 0
            or any(record[key] is not True for key in ("panel_update_injected", "external_active",
                                                     "external_target", "prompt_unchanged"))):
        return False
    events = record["events"]
    return (isinstance(events, list) and len(events) == len("yesterday")
            and all(isinstance(row, dict) and set(row) == {"keyval", "code"}
                    and type(row["keyval"]) is int and row["keyval"] == ord(char)
                    and type(row["code"]) is int and row["code"] > 0
                    for row, char in zip(events, "yesterday")))


def native_repeat_delivered(events: object, *, released: bool) -> bool:
    if not isinstance(events, list) or not 1 <= len(events) <= 128 or released is not True:
        return False
    keys = {"pending_id", "reviewed_id", "code", "held_before", "blocked", "accepted"}
    if any(not isinstance(row, dict) or set(row) != keys or type(row["code"]) is not int
           or any(type(row[key]) is not bool for key in ("held_before", "blocked", "accepted"))
           for row in events):
        return False
    initial = next(((index, row) for index, row in enumerate(events) if row["pending_id"] == "held_f6_a"
                    and row["reviewed_id"] is None and row["code"] > 0
                    and not row["held_before"] and not row["blocked"] and row["accepted"]), None)
    if initial is None:
        return False
    index, first = initial
    return any(row["pending_id"] == "held_f6_b" and row["reviewed_id"] is None
               and row["code"] == first["code"] and row["held_before"]
               and not row["blocked"] and not row["accepted"] for row in events[index + 1:])


def adversarial_guards_checked(record: object) -> bool:
    """Synthetic methods are required separately and cannot stand in for native keys."""
    from phase3_callback_guards import GUARD_CASES, MODIFIER_NAMES
    return (isinstance(record, dict) and set(record) == {'mode', 'cases', 'modifier_masks'}
            and record['mode'] == 'synthetic-actual-methods' and record['cases'] == list(GUARD_CASES)
            and record['modifier_masks'] == list(MODIFIER_NAMES))


def native_held_transition_delivered(record: object) -> bool:
    """Validate a held transition and fresh review; never infer delivered autorepeat."""
    keys = {'mode', 'events', 'typed_events', 'release_events', 'before_fresh_b', 'fresh_b_start_index',
            'repeat_after_b_delivered', 'fresh_b_denied', 'arm', 'released', 'overflow', 'responses', 'fresh_b_armed'}
    if not isinstance(record, dict) or set(record) != keys or record['mode'] != 'native-held-transition':
        return False
    if (record['overflow'] is not False or any(record[key] is not True
            for key in ('released', 'fresh_b_denied', 'fresh_b_armed'))
            or record['arm'] != {'request_id': 'held_f6_a', 'active': True, 'armed': True}
            or not isinstance(record['arm'], dict)
            or any(record['arm'][key] is not True for key in ('active', 'armed'))):
        return False
    before = record['before_fresh_b']
    if (not isinstance(before, dict) or set(before) != {'a_allowed', 'b_responses', 'reviewed', 'prompt_text', 'model_text'}
            or type(before['a_allowed']) is not int or before['a_allowed'] != 1
            or type(before['b_responses']) is not int or before['b_responses'] != 0
            or before['reviewed'] is not False or before['prompt_text'] != 'yesterday' or before['model_text'] != 'yesterday'):
        return False
    expected = [{'request_id': 'held_f6_a', 'decision': 'allow', 'client': 'exo-surface', 'binding': 'fa'},
                {'request_id': 'held_f6_b', 'decision': 'deny', 'client': 'exo-surface', 'binding': 'fb'}]
    if record['responses'] != expected:
        return False
    rows, cut = record['events'], record['fresh_b_start_index']
    row_keys = {'pending_id', 'reviewed_id', 'code', 'held_before', 'blocked', 'accepted'}
    if (not isinstance(rows, list) or not 2 <= len(rows) <= 128 or type(cut) is not int or not 1 <= cut < len(rows)
            or any(not isinstance(row, dict) or set(row) != row_keys or type(row['code']) is not int or row['code'] <= 0
                   or row['pending_id'] not in ('held_f6_a', 'held_f6_b')
                   or row['reviewed_id'] not in (None, 'held_f6_a', 'held_f6_b')
                   or any(type(row[key]) is not bool for key in ('held_before', 'blocked', 'accepted')) for row in rows)):
        return False
    first = rows[0]
    if (first['pending_id'] != 'held_f6_a' or first['reviewed_id'] is not None or first['held_before']
            or first['blocked'] or not first['accepted']):
        return False
    if any(row['accepted'] and (row['held_before'] or row['blocked']) for row in rows):
        return False
    if any(row['accepted'] or not row['held_before'] or row['code'] != first['code'] for row in rows[1:cut]):
        return False
    fresh = rows[cut:]
    if (len(fresh) != 1 or fresh[0]['pending_id'] != 'held_f6_b' or fresh[0]['reviewed_id'] is not None
            or fresh[0]['held_before'] or fresh[0]['blocked'] or not fresh[0]['accepted']):
        return False
    repeats = [row for row in rows[:cut] if row['pending_id'] == 'held_f6_b' and row['held_before']]
    if type(record['repeat_after_b_delivered']) is not bool or record['repeat_after_b_delivered'] is not bool(repeats):
        return False
    typed = record['typed_events']
    if (not isinstance(typed, list) or len(typed) != 9 or any(not isinstance(row, dict)
            or set(row) != {'keyval', 'code', 'f6_held', 'reviewed'} or type(row['keyval']) is not int
            or row['keyval'] != ord(char) or type(row['code']) is not int or row['code'] <= 0
            or row['f6_held'] is not True or row['reviewed'] is not False for row, char in zip(typed, 'yesterday'))):
        return False
    releases = record['release_events']
    return (isinstance(releases, list) and 1 <= len(releases) <= 32
            and all(isinstance(row, dict) and set(row) == {'code', 'keyval'}
                    and type(row['code']) is int and row['code'] > 0
                    and type(row['keyval']) is int and row['keyval'] == 65475 for row in releases)
            and any(row['code'] == first['code'] for row in releases))

"""Validator fixtures only: fabricated records, never native evidence."""


def held_record():
    return {'mode': 'native-held-transition', 'events': [
        {'pending_id': 'held_f6_a', 'reviewed_id': None, 'code': 9, 'held_before': False, 'blocked': False, 'accepted': True},
        {'pending_id': 'held_f6_b', 'reviewed_id': None, 'code': 8, 'held_before': False, 'blocked': False, 'accepted': True}],
        'typed_events': [{'keyval': ord(char), 'code': 20 + index, 'f6_held': True, 'reviewed': False}
                         for index, char in enumerate('yesterday')],
        'release_events': [{'keyval': 65475, 'code': 9}],
        'before_fresh_b': {'a_allowed': 1, 'b_responses': 0, 'reviewed': False, 'prompt_text': 'yesterday', 'model_text': 'yesterday'},
        'fresh_b_start_index': 1, 'repeat_after_b_delivered': False, 'fresh_b_denied': True,
        'arm': {'request_id': 'held_f6_a', 'active': True, 'armed': True}, 'released': True, 'overflow': False,
        'responses': [{'request_id': 'held_f6_a', 'decision': 'allow', 'client': 'exo-surface', 'binding': 'fa'},
                      {'request_id': 'held_f6_b', 'decision': 'deny', 'client': 'exo-surface', 'binding': 'fb'}],
        'fresh_b_armed': True}


def guard_record():
    return {'mode': 'synthetic-actual-methods', 'cases': ['repeat-remap', 'modified-hold', 'held-answer', 'activation-loss'],
            'modifier_masks': ['SHIFT_MASK', 'CONTROL_MASK', 'ALT_MASK', 'SUPER_MASK', 'META_MASK']}

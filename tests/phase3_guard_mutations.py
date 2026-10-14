"""Proposal only: exercise exact c2 UI/model methods under minimal host widgets.

Callbacks below are explicitly synthetic. They do not prove GTK focus, native
repeat delivery or physical UID/key behavior.
"""
import ast
import importlib.util
import json
import sys
from enum import IntFlag
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from exo.protocol import schema as s
from exo_os.profile import Profile
from exo_os.surface.model import ApprovalCard, SurfaceModel

ROOT = Path(__file__).parent
SOURCE = ROOT.parent / 'src/exo_os/surface/ui.py'


class Window:
    def get_mapped(self):
        return self.visible

    def get_visible(self):
        return self.visible

    def set_visible(self, value):
        self.visible = value

    def update_property(self, *_args):
        pass


class Modifiers(IntFlag):
    SHIFT_MASK = 1
    CONTROL_MASK = 2
    ALT_MASK = 4
    SUPER_MASK = 8
    META_MASK = 16


def load_ui(source, name):
    gi = ModuleType('gi')

    def require_version(kind, _version):
        if kind == 'Gtk4LayerShell':
            raise ValueError('no native layer shell in this host prototype')

    gi.require_version = require_version
    repository = ModuleType('gi.repository')
    repository.Gtk = SimpleNamespace(Window=Window, Button=lambda **kwargs: MagicMock(),
                                    AccessibleProperty=SimpleNamespace(LABEL='label'))
    repository.Gdk = SimpleNamespace(ModifierType=Modifiers, KEY_F6=65475, KEY_y=121, KEY_n=110,
                                    KEY_Escape=65307, KEY_Up=65362, KEY_Down=65364,
                                    keyval_name=lambda value: {121: 'y', 110: 'n', 65475: 'F6'}.get(value, ''))
    clock = SimpleNamespace(now=10_000_000)
    repository.GLib = SimpleNamespace(get_monotonic_time=lambda: clock.now, idle_add=lambda callback: None)
    repository.Pango = SimpleNamespace()
    sys.modules['gi'], sys.modules['gi.repository'] = gi, repository
    path = ROOT / 'mutant-sources' / (name + '.py')
    path.parent.mkdir(exist_ok=True)
    path.write_text(source)
    spec = importlib.util.spec_from_file_location('exo_os.surface._held_guard_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, clock


def make_surface(ui):
    surface = ui.SurfaceUI.__new__(ui.SurfaceUI)
    surface.model = SurfaceModel(Profile.default())
    surface.model.set_session('s_0000000000000001')
    surface.model.set_connected(True)
    surface.model.approvals.extend([ApprovalCard('held_f6_a', 'A', binding='fa'),
                                    ApprovalCard('held_f6_b', 'B', binding='fb')])
    sent = []
    surface.send = sent.append
    surface.bar = MagicMock()
    surface.bar.get_visible.return_value = True
    surface.bar.get_focus.return_value = None
    surface.drawer = MagicMock()
    surface.drawer.interactive = False
    surface.drawer.get_visible.return_value = False
    card = ui.Card.__new__(ui.Card)
    surface.card = card
    card.token, card.reviewing, card.layered = None, False, True
    card.visible, card.shown_at = False, 0
    card.pressed_keys, card.review_keys = set(), set()
    card.summary, card.review_hint, card.detail, card.detail_scroll = [MagicMock() for _ in range(4)]
    card.buttons = MagicMock()
    card.buttons.get_first_child.return_value = None
    card.options = []
    card.is_active = lambda: False  # no invented compositor activation
    card.present = lambda: card.set_visible(True)
    card.on_answer = surface._answer
    card.on_review_lost = surface._review_lost
    card.on_defer = surface._defer
    entry = SimpleNamespace(text='')
    entry.get_text = lambda: entry.text

    def set_text(text):
        entry.text = text
        surface.render(surface.model.set_input(text))

    entry.set_text, entry.grab_focus = set_text, lambda: None
    surface.bar.entry = entry
    surface.render({'approval'})
    return surface, sent


def arm(clock, ui):
    clock.now += (ui.ARM_MS + 1) * 1000


def check(condition, reason, surface, sent):
    if not condition:
        pending, reviewed = surface.model.approval, surface.model.reviewed_approval
        raise AssertionError(json.dumps({'reason': reason, 'pending': pending.request_id if pending else None,
                                         'reviewed': reviewed.request_id if reviewed else None,
                                         'review_keys': sorted(surface.card.review_keys),
                                         'sent': [str(body) for body in sent]}))


def held_transition_and_adversarial_repeat(ui, clock):
    surface, sent = make_surface(ui)
    card, model = surface.card, surface.model
    surface._bar_key(None, ui.Gdk.KEY_F6, 9, 0)
    check(model.reviewed_approval.request_id == 'held_f6_a', 'initial F6 must review A', surface, sent)
    arm(clock, ui)
    card._key(None, ui.Gdk.KEY_F6, 9, 0)
    card._key(None, ui.Gdk.KEY_y, 11, 0)
    expected = s.ApprovalRespond(request_id='held_f6_a', decision='allow', client='exo-surface', binding='fa')
    check(sent == [expected] and model.reviewed_approval is None, 'answer A without reviewing B', surface, sent)
    check(model.approval.request_id == 'held_f6_b', 'B must remain pending after A', surface, sent)
    card._key_released(None, ui.Gdk.KEY_y, 11, 0)
    for method in (surface._bar_key, surface._drawer_key):
        method(None, ui.Gdk.KEY_F6, 9, 0)  # explicitly adversarial callback, never labeled native
        check(model.reviewed_approval is None and sent == [expected], 'same held F6 must not review B', surface, sent)
    check(card.review_keys == {9}, 'held state must survive A-to-B and unrelated releases', surface, sent)
    for index, char in enumerate('yesterday'):
        code = 100 + index
        surface._bar_key(None, ord(char), code, 0)
        surface.bar.entry.set_text(surface.bar.entry.text + char)
        card._key_released(None, ord(char), code, 0)
        check(card.review_keys == {9} and model.reviewed_approval is None and sent == [expected],
              'typing with F6 held must not transfer consent', surface, sent)
    check(surface.bar.entry.get_text() == model.text == 'yesterday', 'complete prompt/model text', surface, sent)
    card._key_released(None, ui.Gdk.KEY_F6, 9, 0)
    check(not card.review_keys, 'F6 release must clear its physical latch', surface, sent)
    surface._drawer_key(None, ui.Gdk.KEY_F6, 9, 0)
    check(model.reviewed_approval.request_id == 'held_f6_b', 'new fresh gesture may review B', surface, sent)
    arm(clock, ui)
    card._key(None, ui.Gdk.KEY_n, 12, 0)
    check(sent == [expected, s.ApprovalRespond(request_id='held_f6_b', decision='deny', client='exo-surface', binding='fb')],
          'fresh gesture answers exact B only', surface, sent)


def held_answer_key(ui, clock):
    surface, sent = make_surface(ui)
    card, model = surface.card, surface.model
    surface._bar_key(None, ui.Gdk.KEY_F6, 9, 0)
    arm(clock, ui)
    card._key(None, ui.Gdk.KEY_y, 11, 0)
    card._key_released(None, ui.Gdk.KEY_F6, 9, 0)
    surface._bar_key(None, ui.Gdk.KEY_F6, 9, 0)
    arm(clock, ui)
    card._key(None, ui.Gdk.KEY_y, 11, 0)
    check(len(sent) == 1 and model.reviewed_approval.request_id == 'held_f6_b',
          'held answer key cannot answer B even after deliberate fresh review', surface, sent)
    card._key_released(None, ui.Gdk.KEY_y, 11, 0)
    card._key(None, ui.Gdk.KEY_y, 11, 0)
    check(len(sent) == 2 and sent[-1].request_id == 'held_f6_b', 'released answer key may answer reviewed B', surface, sent)


def modified_hold(ui, clock):
    for modifier in Modifiers:
        surface, sent = make_surface(ui)
        card, model = surface.card, surface.model
        surface._bar_key(None, ui.Gdk.KEY_F6, 9, modifier)
        check(model.reviewed_approval is None, 'modified F6 must be blocked', surface, sent)
        surface._drawer_key(None, ui.Gdk.KEY_F6, 9, 0)
        card._key(None, ui.Gdk.KEY_F6, 9, 0)
        check(model.reviewed_approval is None and not sent,
              'modifier release does not turn held F6 into fresh review', surface, sent)
        check(card.review_keys == {9}, 'modified physical hold is retained', surface, sent)
        card._key_released(None, ui.Gdk.KEY_F6, 9, 0)
        surface._bar_key(None, ui.Gdk.KEY_F6, 9, 0)
        check(model.reviewed_approval.request_id == 'held_f6_a', 'fresh post-release gesture accepted', surface, sent)


def focus_loss_hold(ui, clock):
    surface, sent = make_surface(ui)
    surface._bar_key(None, ui.Gdk.KEY_F6, 9, 0)
    surface._review_lost()  # actual lifecycle method; focus event itself is not simulated as native
    surface._drawer_key(None, ui.Gdk.KEY_F6, 9, 0)
    check(surface.model.reviewed_approval is None and surface.card.review_keys == {9} and not sent,
          'focus loss must not make an unreleased F6 fresh', surface, sent)


def replace_method(source, cls_name, name, before, after):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    lines = source.splitlines(keepends=True)
    block = ''.join(lines[method.lineno - 1:method.end_lineno])
    assert block.count(before) == 1, (name, before)
    lines[method.lineno - 1:method.end_lineno] = [block.replace(before, after)]
    return ''.join(lines)


MUTATIONS = [
    ('ignore_f6_repeat', 'Card', 'fresh_review_key', 'return not repeated and not (state and state & blocked)', 'return not (state and state & blocked)'),
    ('ignore_f6_modifiers', 'Card', 'fresh_review_key', 'return not repeated and not (state and state & blocked)', 'return not repeated'),
    ('forget_modified_hold', 'Card', 'fresh_review_key', 'self.review_keys.add(physical)', 'if state:\n            return False\n        self.review_keys.add(physical)'),
    ('clear_on_remap', 'Card', 'show_card', 'if changed:\n            self.shown_at', 'if changed:\n            self.review_keys.clear()\n            self.shown_at'),
    ('release_all_latches', 'Card', '_key_released', 'self.review_keys.discard(physical)', 'self.review_keys.clear()'),
    ('ignore_held_answer_key', 'Card', '_key', 'if not repeated:\n                self._answer(action)', 'if True:\n                self._answer(action)'),
    ('bar_bypasses_freshness', 'SurfaceUI', '_bar_key', 'if self.card.fresh_review_key(keyval, code, _state):', 'if self.card.fresh_review_key(keyval, code, _state) or True:'),
    ('drawer_bypasses_freshness', 'SurfaceUI', '_drawer_key', 'if self.card.fresh_review_key(keyval, code, _state):', 'if self.card.fresh_review_key(keyval, code, _state) or True:'),
    ('repeat_restarts_arm', 'Card', '_key', 'if keyval == Gdk.KEY_F6:\n            self.fresh_review_key', 'if keyval == Gdk.KEY_F6:\n            self.shown_at = GLib.get_monotonic_time()\n            self.fresh_review_key'),
    ('answer_auto_reviews_b', 'SurfaceUI', '_answer', 'self.render({"approval"})', 'self.render({"approval"})\n        if self.model.approval is not None:\n            self.review_approval()'),
    ('focus_loss_clears_hold', 'SurfaceUI', '_review_lost', 'self.model.leave_approval_review()', 'self.card.review_keys.clear()\n        self.model.leave_approval_review()'),
]
CASES = [held_transition_and_adversarial_repeat, held_answer_key, modified_hold, focus_loss_hold]


def run(source, name):
    ui, clock = load_ui(source, name)
    for case in CASES:
        case(ui, clock)
    return {'case_count': len(CASES), 'modified_masks': len(Modifiers), 'mode': 'host-synthetic-real-methods'}

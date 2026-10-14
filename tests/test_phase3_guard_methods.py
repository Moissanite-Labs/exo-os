"""Real c2 methods under minimal host widgets; explicitly not GTK/native proof."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from phase3_callback_guards import callback_guards  # noqa: E402
from phase3_input_evidence import adversarial_guards_checked  # noqa: E402
import phase3_guard_mutations as guards  # noqa: E402


@pytest.fixture(autouse=True)
def restore_gi_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(guards, "ROOT", tmp_path)
    previous = {name: sys.modules.get(name) for name in ('gi', 'gi.repository')}
    yield
    for name, value in previous.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


def test_actual_method_baseline_and_every_backend_callback_guard():
    source = guards.SOURCE.read_text()
    assert guards.run(source, 'baseline')['case_count'] == 4
    ui, clock = guards.load_ui(source, 'callback_baseline')
    surface, sent = guards.make_surface(ui)
    surface.model.approvals.clear()
    surface.render({'approval'})
    # Minimal surface implements actual event/model methods. No native activation is asserted.
    surface.show = lambda: None
    from exo.protocol import schema as s
    seq = [0]

    def event(body):
        seq[0] += 1
        return s.Event.of(body, seq[0], surface.model.session)

    original_clock = ui.GLib.get_monotonic_time
    assert adversarial_guards_checked(callback_guards(surface, ui, s, event, sent))
    assert ui.GLib.get_monotonic_time is original_clock


@pytest.mark.parametrize('mutation', guards.MUTATIONS, ids=[row[0] for row in guards.MUTATIONS])
def test_real_guard_mutations_are_killed(mutation):
    name, cls, method, before, after = mutation
    mutated = guards.replace_method(guards.SOURCE.read_text(), cls, method, before, after)
    with pytest.raises(AssertionError):
        guards.run(mutated, name)

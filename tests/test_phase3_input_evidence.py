"""A discarded duplicate key press cannot masquerade as tested native repeat rejection."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from phase3_input_evidence import native_repeat_delivered  # noqa: E402


def events():
    return [{"pending_id": "held_f6_a", "reviewed_id": None, "code": 77,
             "held_before": False, "blocked": False, "accepted": True},
            {"pending_id": "held_f6_b", "reviewed_id": None, "code": 77,
             "held_before": True, "blocked": False, "accepted": False}]


def test_requires_actual_repeat_delivery_and_observed_release():
    assert native_repeat_delivered(events(), released=True)
    assert not native_repeat_delivered(events()[:1], released=True)
    assert not native_repeat_delivered(events(), released=False)
    assert not native_repeat_delivered(list(reversed(events())), released=True)


@pytest.mark.parametrize("index,change", [(0, {"code": 0}), (1, {"code": 78}), (1, {"held_before": False}),
    (1, {"blocked": True}), (1, {"accepted": True}), (1, {"accepted": None}),
    (1, {"reviewed_id": "held_f6_b"}), (1, {"pending_id": "held_f6_a"}), (1, {"code": True})])
def test_wrong_input_context_or_original_outcome_cannot_pass(index, change):
    rows = copy.deepcopy(events())
    rows[index].update(change)
    assert not native_repeat_delivered(rows, released=True)


def test_standalone_probe_staging_includes_its_observer_module():
    root = Path(__file__).resolve().parents[1]
    assert "for file in probe-surface-gtk.py phase3_input_evidence.py" in root.joinpath("tools/accept-phase3.sh").read_text()
    assert "image/mkosi.postinst.chroot tools pyproject.toml" in root.joinpath("tools/test-linux.sh").read_text()

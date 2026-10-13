"""A discarded duplicate key press cannot masquerade as tested native repeat rejection."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from phase3_input_evidence import (PROMPT_MARKER, native_external_panel_delivered, native_prompt_delivered,
                                  native_repeat_delivered)  # noqa: E402


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


def prompt_record():
    return {"stage": "unsolicited-panels", "text": PROMPT_MARKER, "model_text": PROMPT_MARKER,
            "events": [{"keyval": ord(char), "code": 8 + index} for index, char in enumerate(PROMPT_MARKER)],
            "sent_delta": 0, "review_unchanged": True, "reviewed": False, "entry_target": True, "mapped": True,
            "gtk_active": False, "panel_update_injected": False}


def test_native_prompt_requires_received_keys_and_text_even_with_stale_gtk_activation():
    assert native_prompt_delivered(prompt_record())
    actual = prompt_record()
    actual["gtk_active"] = True
    assert native_prompt_delivered(actual)


@pytest.mark.parametrize("patch", [{"text": ""}, {"model_text": ""}, {"events": []}, {"sent_delta": True},
    {"sent_delta": 1}, {"mapped": 1}, {"entry_target": False}, {"review_unchanged": False},
    {"reviewed": True}, {"gtk_active": 0}, {"stage": "unknown"}, {"panel_update_injected": True}, {"extra": "unchecked"}])
def test_native_prompt_rejects_incomplete_or_malformed_delivery(patch):
    actual = prompt_record()
    actual.update(patch)
    assert not native_prompt_delivered(actual)


@pytest.mark.parametrize("patch", [{"code": 0}, {"code": True}, {"code": -1}, {"keyval": 0},
    {"keyval": True}, {"extra": 1}])
def test_native_prompt_requires_positive_physical_codes_and_exact_marker_key_sequence(patch):
    actual = prompt_record()
    actual["events"][0].update(patch)
    assert not native_prompt_delivered(actual)


def test_native_panel_update_must_be_injected_during_typing():
    actual = prompt_record()
    actual["stage"] = "unsolicited-panel-update"
    assert not native_prompt_delivered(actual)
    actual["panel_update_injected"] = True
    assert native_prompt_delivered(actual)


def external_record():
    return {"mode": "native", "text": "yesterday", "events": [
        {"keyval": ord(char), "code": 8 + index} for index, char in enumerate("yesterday")],
        "panel_update_injected": True, "sent_delta": 0, "reviewed": False,
        "external_active": True, "external_target": True, "prompt_unchanged": True}


def test_native_external_panel_requires_actual_received_text_and_keys():
    assert native_external_panel_delivered(external_record())
    actual = external_record()
    actual["events"][0]["code"] = 0
    assert not native_external_panel_delivered(actual)


@pytest.mark.parametrize("patch", [{"mode": "synthetic"}, {"text": "yest"}, {"events": []},
    {"panel_update_injected": False}, {"sent_delta": True}, {"sent_delta": 1}, {"reviewed": True},
    {"external_active": False}, {"external_target": 1}, {"prompt_unchanged": False}, {"extra": "unchecked"}])
def test_native_external_panel_rejects_incomplete_input_or_consent_changes(patch):
    actual = external_record()
    actual.update(patch)
    assert not native_external_panel_delivered(actual)

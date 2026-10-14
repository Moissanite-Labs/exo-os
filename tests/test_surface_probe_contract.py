"""The phase 3 harness must refuse incomplete or unlayered GTK evidence."""
import json
import copy
from phase3_records import held_record, guard_record
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
VALIDATOR = TOOLS / "validate-surface-probe.py"
PROBE = TOOLS / "probe-surface-gtk.py"
STAGES = ("unsolicited-panels", "panels-close", "updated-panels-close", "unsolicited-panel-update",
          "approval-defer", "external-work-return", "generic-answer-return")
MARKER = "exo focus marker"
PASS = {"probe": "exo-surface-gtk-v3", "backend": "wayland-layer", "layered": True, "status": "PASS",
        "gsk_renderer": "cairo", "renderers": {name: "GskCairoRenderer" for name in ("bar", "drawer", "card")},
        "external_panel_update": {"mode": "native", "text": "yesterday", "events": [
            {"keyval": ord(char), "code": 8 + index} for index, char in enumerate("yesterday")],
            "panel_update_injected": True, "sent_delta": 0, "reviewed": False,
            "external_active": True, "external_target": True, "prompt_unchanged": True},
        "prompt_delivery": {"mode": "native", "checks": [{"stage": stage, "text": MARKER,
            "model_text": MARKER, "events": [{"keyval": ord(char), "code": 8 + index} for index, char in enumerate(MARKER)],
            "sent_delta": 0, "review_unchanged": True, "reviewed": False, "entry_target": True, "mapped": True,
            "gtk_active": False, "panel_update_injected": stage == "unsolicited-panel-update"} for stage in STAGES]},
        "held_f6": held_record(), "adversarial_guards": guard_record()}


def validate_result(payload, exit_code=0, backend="wayland-layer"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(VALIDATOR), "--backend", backend,
                           "--exit-code", str(exit_code)], input=text, text=True,
                          capture_output=True, timeout=10)


def validate(payload, exit_code=0):
    return validate_result(payload, exit_code).returncode


def test_phase3_accepts_only_complete_layered_probe_evidence():
    assert validate(PASS) == 0
    for bad in ("", "SMOKE OK layer-shell", "{}", {**PASS, "status": "FAIL"},
                {**PASS, "layered": False}, {**PASS, "layered": 1}, {**PASS, "backend": "broadway-private"},
                {"probe": PASS["probe"], "status": "PASS", "layered": True},
                {**PASS, "gsk_renderer": "gl"},
                {**PASS, "renderers": {**PASS["renderers"], "card": "GskGLRenderer"}},
                {**PASS, "renderers": {"bar": "GskCairoRenderer"}},
                {**PASS, "renderers": {**PASS["renderers"], "drawer": None}},
                {**PASS, "held_f6": {"mode": "synthetic"}},
                {**PASS, "held_f6": {**PASS["held_f6"], "events": PASS["held_f6"]["events"][:1]}},
                {**PASS, "held_f6": {**PASS["held_f6"], "b_responses": 1}},
                {**PASS, "held_f6": {**PASS["held_f6"], "released": False}},
                {**PASS, "probe": "exo-surface-gtk-v1"}, {**PASS, "probe": "exo-surface-gtk-v2"},
                {**PASS, "adversarial_guards": {"mode": "native"}},
                {**PASS, "prompt_delivery": {"mode": "synthetic"}},
                {**PASS, "prompt_delivery": {"mode": "native", "checks": PASS["prompt_delivery"]["checks"][:-1]}},
                {**PASS, "prompt_delivery": {"mode": "native", "checks": list(reversed(PASS["prompt_delivery"]["checks"]))}},
                {**PASS, "external_panel_update": {"mode": "synthetic"}},
                {**PASS, "unchecked": True}):
        run = validate_result(bad)
        assert run.returncode != 0 and run.stdout == "", bad
    run = validate_result(PASS, exit_code=1)
    assert run.returncode != 0 and run.stdout == ""  # printed success cannot mask a failed process


def test_native_output_does_not_claim_undelivered_repeat_was_rejected():
    run = validate_result(PASS)
    assert run.returncode == 0
    assert "native held transition" in run.stdout
    assert "repeat not delivered (not tested)" in run.stdout
    assert "repeat delivered/rejected" not in run.stdout
    assert "adversarial guards: synthetic-actual-methods" in run.stdout


def test_delivered_repeat_is_reported_only_when_rejection_validates():
    payload = copy.deepcopy(PASS)
    held = payload["held_f6"]
    held["events"].insert(1, {"pending_id": "held_f6_b", "reviewed_id": None, "code": 9,
                             "held_before": True, "blocked": False, "accepted": False})
    held["fresh_b_start_index"] = 2
    held["repeat_after_b_delivered"] = True
    run = validate_result(payload)
    assert run.returncode == 0
    assert "repeat delivered/rejected" in run.stdout
    assert "repeat not delivered" not in run.stdout
    assert "adversarial guards: synthetic-actual-methods" in run.stdout
    held["events"][1]["accepted"] = True
    run = validate_result(payload)
    assert run.returncode != 0 and run.stdout == ""


def test_native_keyboard_evidence_rejects_local_focus_without_real_keys_or_unintended_actions():
    for patch in ({"events": []}, {"events": [{"keyval": ord(char), "code": 0} for char in MARKER]},
                  {"sent_delta": 1}, {"review_unchanged": False}, {"reviewed": True}, {"model_text": ""},
                  {"entry_target": False}, {"mapped": False}, {"panel_update_injected": True}):
        payload = copy.deepcopy(PASS)
        payload["prompt_delivery"]["checks"][0].update(patch)
        assert validate(payload) != 0


def test_private_broadway_result_explicitly_reports_synthetic_keyboard_coverage():
    payload = {**PASS, "backend": "broadway-private", "layered": False, "gsk_renderer": "default",
               "held_f6": {"mode": "synthetic"}, "prompt_delivery": {"mode": "synthetic"},
               "external_panel_update": {"mode": "synthetic"}}
    run = validate_result(payload, backend="broadway-private")
    assert run.returncode == 0
    assert "keyboard coverage: synthetic" in run.stdout
    assert "adversarial guards: synthetic-actual-methods" in run.stdout
    assert "native held transition" not in run.stdout and "repeat delivered/rejected" not in run.stdout


def test_external_panel_update_requires_observed_typing_without_stealing_focus_or_answering():
    for patch in ({"events": []}, {"text": "yest"}, {"panel_update_injected": False}, {"sent_delta": 1},
                  {"reviewed": True}, {"external_active": False}, {"external_target": False},
                  {"prompt_unchanged": False}):
        payload = copy.deepcopy(PASS)
        payload["external_panel_update"].update(patch)
        assert validate(payload) != 0


def test_shared_probe_refuses_an_inherited_desktop_or_missing_vm_opt_in():
    env = {"PATH": os.environ.get("PATH", ""), "GDK_BACKEND": "wayland", "WAYLAND_DISPLAY": "wayland-1"}
    for args in ([], ["--vm-acceptance"]):
        result = subprocess.run([sys.executable, str(PROBE), *args], env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and "disposable" in result.stderr
        assert result.stdout == ""


def test_schema3_rejects_old_results_missing_guards_and_oversized_output():
    for version in ('exo-surface-gtk-v1', 'exo-surface-gtk-v2'):
        assert validate({**PASS, 'probe': version}) != 0
    old = copy.deepcopy(PASS)
    old.pop('adversarial_guards')
    assert validate(old) != 0
    assert validate(json.dumps(PASS) + ' ' * 65536) != 0

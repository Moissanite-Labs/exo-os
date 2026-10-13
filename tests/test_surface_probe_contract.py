"""The phase 3 harness must refuse incomplete or unlayered GTK evidence."""
import json
import copy
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
PASS = {"probe": "exo-surface-gtk-v2", "backend": "wayland-layer", "layered": True, "status": "PASS",
        "gsk_renderer": "cairo", "renderers": {name: "GskCairoRenderer" for name in ("bar", "drawer", "card")},
        "external_panel_update": {"mode": "native", "text": "yesterday", "events": [
            {"keyval": ord(char), "code": 8 + index} for index, char in enumerate("yesterday")],
            "panel_update_injected": True, "sent_delta": 0, "reviewed": False,
            "external_active": True, "external_target": True, "prompt_unchanged": True},
        "prompt_delivery": {"mode": "native", "checks": [{"stage": stage, "text": MARKER,
            "model_text": MARKER, "events": [{"keyval": ord(char), "code": 8 + index} for index, char in enumerate(MARKER)],
            "sent_delta": 0, "review_unchanged": True, "reviewed": False, "entry_target": True, "mapped": True,
            "gtk_active": False, "panel_update_injected": stage == "unsolicited-panel-update"} for stage in STAGES]},
        "held_f6": {"mode": "native", "released": True, "a_allowed": 1, "b_responses": 0,
                    "reviewed": False, "prompt_text": "yesterday", "events": [
                        {"pending_id": "held_f6_a", "reviewed_id": None, "code": 77,
                         "held_before": False, "blocked": False, "accepted": True},
                        {"pending_id": "held_f6_b", "reviewed_id": None, "code": 77,
                         "held_before": True, "blocked": False, "accepted": False}]}}


def validate(payload, exit_code=0):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(VALIDATOR), "--backend", "wayland-layer",
                           "--exit-code", str(exit_code)], input=text, text=True,
                          capture_output=True, timeout=10).returncode


def test_phase3_accepts_only_complete_layered_probe_evidence():
    assert validate(PASS) == 0
    for bad in ("", "SMOKE OK layer-shell", "{}", {**PASS, "status": "FAIL"},
                {**PASS, "layered": False}, {**PASS, "backend": "broadway-private"},
                {"probe": PASS["probe"], "status": "PASS", "layered": True},
                {**PASS, "gsk_renderer": "gl"},
                {**PASS, "renderers": {**PASS["renderers"], "card": "GskGLRenderer"}},
                {**PASS, "renderers": {"bar": "GskCairoRenderer"}},
                {**PASS, "renderers": {**PASS["renderers"], "drawer": None}},
                {**PASS, "held_f6": {"mode": "synthetic"}},
                {**PASS, "held_f6": {**PASS["held_f6"], "events": PASS["held_f6"]["events"][:1]}},
                {**PASS, "held_f6": {**PASS["held_f6"], "b_responses": 1}},
                {**PASS, "held_f6": {**PASS["held_f6"], "released": False}},
                {**PASS, "probe": "exo-surface-gtk-v1"},
                {**PASS, "prompt_delivery": {"mode": "synthetic"}},
                {**PASS, "prompt_delivery": {"mode": "native", "checks": PASS["prompt_delivery"]["checks"][:-1]}},
                {**PASS, "prompt_delivery": {"mode": "native", "checks": list(reversed(PASS["prompt_delivery"]["checks"]))}},
                {**PASS, "external_panel_update": {"mode": "synthetic"}},
                {**PASS, "unchecked": True}):
        assert validate(bad) != 0, bad
    assert validate(PASS, exit_code=1) != 0  # a printed success cannot mask a failed process


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
    run = subprocess.run([sys.executable, str(VALIDATOR), "--backend", "broadway-private", "--exit-code", "0"],
                         input=json.dumps(payload), capture_output=True, text=True, timeout=10)
    assert run.returncode == 0


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

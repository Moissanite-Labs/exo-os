"""The phase 3 harness must refuse incomplete or unlayered GTK evidence."""
import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
VALIDATOR = TOOLS / "validate-surface-probe.py"
PROBE = TOOLS / "probe-surface-gtk.py"
PASS = {"probe": "exo-surface-gtk-v1", "backend": "wayland-layer", "layered": True, "status": "PASS",
        "gsk_renderer": "cairo", "renderers": {name: "GskCairoRenderer" for name in ("bar", "drawer", "card")}}


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
                {**PASS, "unchecked": True}):
        assert validate(bad) != 0, bad
    assert validate(PASS, exit_code=1) != 0  # a printed success cannot mask a failed process


def test_shared_probe_refuses_an_inherited_desktop_or_missing_vm_opt_in():
    env = {"PATH": os.environ.get("PATH", ""), "GDK_BACKEND": "wayland", "WAYLAND_DISPLAY": "wayland-1"}
    for args in ([], ["--vm-acceptance"]):
        result = subprocess.run([sys.executable, str(PROBE), *args], env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and "disposable" in result.stderr
        assert result.stdout == ""

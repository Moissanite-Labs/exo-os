"""Production renderer policy (OS-20, #107): probe GL, fall back to Cairo, log the choice. Fakes only; no GTK."""
import subprocess
from pathlib import Path

import pytest

from exo_os.surface import renderer

ROOT = Path(__file__).resolve().parents[1]
WAYLAND = {"WAYLAND_DISPLAY": "wayland-1"}


def test_gl_available_leaves_gtk_default_untouched():
    env, lines = dict(WAYLAND), []
    choice = renderer.apply(env, probe=lambda e: (True, "GskNglRenderer"), log=lines.append)
    assert (choice.renderer, choice.source) == ("gl", "probe-ok")
    assert "GSK_RENDERER" not in env
    assert lines == ["exo-surface: renderer=gl source=probe-ok detail=GskNglRenderer"]


def test_gl_failing_forces_cairo_and_says_so():
    env, lines = dict(WAYLAND), []
    choice = renderer.apply(env, probe=lambda e: (False, "probe exited 139"), log=lines.append)
    assert (choice.renderer, choice.source) == ("cairo", "probe-failed")
    assert env["GSK_RENDERER"] == "cairo"
    assert lines == ["exo-surface: renderer=cairo source=probe-failed detail=probe exited 139"]


def test_a_probe_that_raises_falls_back_to_cairo():
    def boom(env):
        raise RuntimeError("secret /home/x")
    env = dict(WAYLAND)
    choice = renderer.apply(env, probe=boom, log=lambda line: None)
    assert choice.renderer == "cairo" and env["GSK_RENDERER"] == "cairo"
    assert "secret" not in choice.detail and "/" not in choice.detail


def test_operator_choice_is_never_probed_or_changed():
    env = {**WAYLAND, "GSK_RENDERER": "cairo"}
    choice = renderer.apply(env, probe=lambda e: pytest.fail("must not probe"), log=lambda line: None)
    assert (choice.renderer, choice.source) == ("default", "environment")
    assert env["GSK_RENDERER"] == "cairo"


@pytest.mark.parametrize("env", [{}, {**WAYLAND, "GDK_BACKEND": "x11"}, {**WAYLAND, "GDK_BACKEND": "broadway"}])
def test_no_wayland_display_means_no_probe_and_no_override(env):
    env = dict(env)
    choice = renderer.apply(env, probe=lambda e: pytest.fail("must not probe"), log=lambda line: None)
    assert choice.source == "no-display" and "GSK_RENDERER" not in env


class Done:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout = code, out


def fake_run(monkeypatch, result):
    seen = {}

    def run(cmd, **kw):
        seen["env"], seen["timeout"] = kw["env"], kw["timeout"]
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(renderer.subprocess, "run", run)
    return seen


@pytest.mark.parametrize("name", sorted(renderer.GL_RENDERERS))
def test_probe_accepts_gl_family_and_strips_any_renderer_override(monkeypatch, name):
    seen = fake_run(monkeypatch, Done(0, f"noise\n{name}\n"))
    assert renderer.run_probe({**WAYLAND, "GSK_RENDERER": "x"}) == (True, name)
    assert "GSK_RENDERER" not in seen["env"] and seen["timeout"] == renderer.TIMEOUT


@pytest.mark.parametrize("result, reason", [
    (Done(0, "GskCairoRenderer\n"), "GskCairoRenderer"),   # GTK itself fell back to software
    (Done(0, ""), "unknown"),
    (Done(139), "exited 139"),                              # crash
    (subprocess.TimeoutExpired("x", 1), "timed out"),       # hang
    (FileNotFoundError("nope"), "did not start"),
])
def test_probe_treats_anything_but_a_working_gl_renderer_as_failure(monkeypatch, result, reason):
    fake_run(monkeypatch, result)
    ok, detail = renderer.run_probe(WAYLAND)
    assert not ok and reason in detail


def test_log_line_round_trips_and_rejects_nonsense():
    for choice in (renderer.Choice("gl", "probe-ok", "GskNglRenderer"), renderer.Choice("cairo", "probe-failed", "x"),
                   renderer.Choice("default", "environment", "GSK_RENDERER=cairo")):
        assert renderer.parse_log_line(choice.log_line())["renderer"] == choice.renderer
    for bad in ("", "exo-surface: renderer=gl source=probe-failed", "renderer=cairo source=probe-ok",
                "exo-surface: renderer=vulkan source=probe-ok"):
        with pytest.raises(ValueError):
            renderer.parse_log_line(bad)


def test_production_unit_sets_no_renderer_and_the_vm_says_why_it_keeps_cairo():
    unit = (ROOT / "image/mkosi.extra/usr/lib/systemd/user/exo-surface.service").read_text()
    assert "GSK_RENDERER" not in unit
    dropin = (ROOT / "image/mkosi.profiles/vmtest/mkosi.extra/usr/lib/systemd/user/exo-surface.service.d/20-renderer.conf")
    text = dropin.read_text()
    assert "Environment=GSK_RENDERER=cairo\n" in text and "no GPU" in text and "OS-20" in text


def test_acceptance_records_the_chosen_renderer():
    source = (ROOT / "tools/accept-phase3.sh").read_text()
    assert "exo_os.surface.renderer" in source and "p3-surface-renderer-choice.json" in source
    assert "parse_log_line" in source

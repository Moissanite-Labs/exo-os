"""Production renderer policy (OS-20, #107): probe GL, fall back to Cairo, log the choice. Fakes only; no GTK."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from exo_os.surface import renderer

ROOT = Path(__file__).resolve().parents[1]
WAYLAND = {"WAYLAND_DISPLAY": "wayland-1"}


def test_gl_available_leaves_gtk_default_untouched():
    env, lines = dict(WAYLAND), []
    choice = renderer.apply(env, probe=lambda e: (True, "GskGLRenderer"), log=lines.append)
    assert (choice.renderer, choice.source) == ("gl", "probe-ok")
    assert "GSK_RENDERER" not in env
    assert lines == ["exo-surface: renderer=gl source=probe-ok detail=GskGLRenderer"]


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


def test_child_emits_gtype_names_and_the_allowlist_holds_the_same_form():
    assert "__gtype__.name" in renderer.PROBE_HELPERS and "__name__" not in renderer.PROBE_CODE
    assert all(re.fullmatch(r"Gsk[A-Za-z0-9]+Renderer", n) for n in renderer.GL_RENDERERS)


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


GOOD = {"renderer": "gl", "source": "probe-ok", "detail": "GskGLRenderer"}
FAILED = {"renderer": "cairo", "source": "probe-failed", "detail": "probe exited 3"}


@pytest.mark.parametrize("record", [GOOD, FAILED])
def test_validator_accepts_probed_records(record):
    assert renderer.validate_choice(record, probed=True) == record


@pytest.mark.parametrize("record", [
    {"renderer": "gl", "source": "invented", "detail": ""},                      # invented source
    {"renderer": "cairo", "source": "probe-ok", "detail": ""},                   # contradictory pair
    {"renderer": "gl", "source": "probe-ok", "detail": {"x": 1}},                # non-string detail
    {"renderer": "default", "source": "no-display", "detail": "no display"},     # probe never ran
    {"renderer": "default", "source": "environment", "detail": "GSK_RENDERER=cairo"},  # probe never ran
    {"renderer": "gl", "source": "probe-ok"},                                    # missing key
    {**GOOD, "extra": "x"}, "text", None,
])
def test_validator_rejects_unprobed_or_malformed_records_for_the_no_override_vm_record(record):
    with pytest.raises(ValueError):
        renderer.validate_choice(record, probed=True)


def test_validator_without_probe_requirement_allows_default_records_but_not_invalid_ones():
    for record in ({"renderer": "default", "source": "no-display", "detail": ""},
                   {"renderer": "default", "source": "environment", "detail": "x"}):
        assert renderer.validate_choice(record) == record
    for record in ({"renderer": "gl", "source": "invented", "detail": ""}, {**GOOD, "detail": 3},
                   {"renderer": "cairo", "source": "probe-ok", "detail": ""}):
        with pytest.raises(ValueError):
            renderer.validate_choice(record)


def test_acceptance_script_requires_a_probed_record():
    assert "validate_choice(json.loads(selection), probed=True)" in (ROOT / "tools/accept-phase3.sh").read_text()


@pytest.mark.skipif(sys.platform != "linux", reason="needs Linux GTK")
def test_real_unrealized_gl_and_vulkan_renderers_keep_the_default_through_the_real_child(monkeypatch):
    pytest.importorskip("gi")
    creators = {"GskGLRenderer": "Gsk.GLRenderer", "GskVulkanRenderer": "Gsk.VulkanRenderer"}
    for gtype, ctor in creators.items():
        code = (renderer.PROBE_HELPERS + "from gi.repository import Gsk\n"
                f"renderer = {ctor}()\nprint(identify(renderer))\n")
        monkeypatch.setattr(renderer, "PROBE_CODE", code)
        assert renderer.run_probe(WAYLAND) == (True, gtype)
    # Through choose(), the GL family keeps GTK's default.
    monkeypatch.setattr(renderer, "PROBE_CODE", renderer.PROBE_HELPERS + "from gi.repository import Gsk\n"
                        "print(identify(Gsk.GLRenderer()))\n")
    env = dict(WAYLAND)
    choice = renderer.apply(env, log=lambda line: None)
    assert (choice.renderer, choice.source) == ("gl", "probe-ok") and "GSK_RENDERER" not in env

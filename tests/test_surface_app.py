"""The GTK side. Widget smoke uses the shared probe on a private Broadway display.

Phase 3 separately runs that probe against the authenticated disposable VM's
Wayland compositor; this pytest launcher never uses an inherited desktop.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("gi")
from exo_os.surface import app  # noqa: E402  (loads Gio only)

SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_needs_preload():
    lib = "libgtk4-layer-shell.so.0"
    wl = {"WAYLAND_DISPLAY": "wayland-1"}
    assert app.needs_preload(wl, lib)
    assert not app.needs_preload(wl, None)  # library not installed: plain windows, no re-exec
    assert not app.needs_preload({**wl, "LD_PRELOAD": "/usr/lib/libgtk4-layer-shell.so"}, lib)
    assert not app.needs_preload({**wl, app.PRELOAD_MARK: "1"}, lib)
    assert not app.needs_preload({**wl, "GDK_BACKEND": "broadway"}, lib)
    assert not app.needs_preload({"DISPLAY": ":0"}, lib)
    env = app.preload_env({"LD_PRELOAD": "libfoo.so"}, "libgtk4-layer-shell.so.0")
    assert env["LD_PRELOAD"] == "libgtk4-layer-shell.so.0:libfoo.so" and env[app.PRELOAD_MARK] == "1"


def test_toggle_path_does_not_load_gtk():
    code = "import sys, exo_os.surface.app; print('gi.repository.Gtk' in sys.modules, 'exo_os.surface.ui' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": SRC}, capture_output=True,
                         text=True, timeout=60)
    assert out.stdout.split() == ["False", "False"], out.stderr


def test_load_profile_with_wrong_shaped_toml_uses_default(tmp_path):
    from exo_os.profile import Profile

    (tmp_path / "machine.toml").write_text("apps = 5\n")  # valid TOML, wrong shape: not a ValueError
    assert app.load_profile(tmp_path / "machine.toml").data == Profile.default().data


def test_unknown_command_is_refused():
    assert app.main(["exo-surface", "dance"]) == 2


def test_load_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_OS_MACHINE", str(tmp_path))
    assert app.load_profile().get("surface.plain") is True  # no file yet: the default
    (tmp_path / "machine.toml").write_text('[surface]\nlanguage = "ur"\n')
    assert app.load_profile().get("surface.language") == "ur"
    # a broken profile falls back to the default instead of keeping the bar shut
    (tmp_path / "machine.toml").write_text("[nonsense]\n")
    assert app.load_profile().get("surface.language") == "en"


PROBE = Path(__file__).resolve().parents[1] / "tools" / "probe-surface-gtk.py"
VALIDATOR = Path(__file__).resolve().parents[1] / "tools" / "validate-surface-probe.py"


def _broadway(tmp_path):
    exe = shutil.which("gtk4-broadwayd")
    if not exe:
        return None, None
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    display = f":{40 + os.getpid() % 50}"
    proc = subprocess.Popen([exe, display, "--unixsocket", str(run / "http.sock")],
                            env={**os.environ, "XDG_RUNTIME_DIR": str(run)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY", "LD_PRELOAD")}
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=display, XDG_RUNTIME_DIR=str(run))
    return proc, env


def test_widgets_smoke(tmp_path):
    proc, env = _broadway(tmp_path)
    if proc is None:
        pytest.skip("no private display: install gtk4-broadwayd")
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    try:
        out = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True, timeout=60)
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(5)
    assert out.returncode == 0, out.stdout + out.stderr
    verified = subprocess.run([sys.executable, str(VALIDATOR), "--backend", "broadway-private",
                               "--exit-code", str(out.returncode)], input=out.stdout, capture_output=True,
                              text=True, timeout=10)
    assert verified.returncode == 0, out.stdout + out.stderr + verified.stderr


def test_review_button_is_outside_real_gtk_tab_navigation(tmp_path):
    """GTK's own focus traversal is required; a fake property cannot certify it."""
    proc, env = _broadway(tmp_path)
    if proc is None:
        pytest.skip("no private display: install gtk4-broadwayd")
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    code = '''import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
assert Gtk.init_check()
from exo.protocol import schema as s
from exo_os.profile import Profile
from exo_os.surface.model import SurfaceModel
from exo_os.surface.ui import SurfaceUI
model = SurfaceModel(Profile.default())
model.set_session("tab-session")
sent = []
ui = SurfaceUI(model, sent.append)
ui.on_connected(True)
ui.show()
ui.on_event(s.Event.of(s.ApprovalRequested(request_id="tab", summary="Pending", binding="bound"), 1, "tab-session"))
ui.bar.entry.set_text("launcher draft")
def pump():
    context = GLib.MainContext.default()
    for _ in range(100):
        if not context.pending(): break
        context.iteration(False)
def inside(target):
    focused = ui.bar.get_focus()
    while focused is not None:
        if focused == target: return True
        focused = focused.get_parent()
    return False
pump()
assert ui.bar.review.get_visible() and not ui.bar.review.get_focusable()
ui.bar.entry.grab_focus()
assert inside(ui.bar.entry)
for direction in (Gtk.DirectionType.TAB_FORWARD, Gtk.DirectionType.TAB_BACKWARD):
    for _ in range(12):
        ui.bar.child_focus(direction)
        pump()
        assert not inside(ui.bar.review)
        assert model.reviewed_approval is None
# The test must detect the reviewed bug when focusability is reintroduced.
ui.bar.review.set_focusable(True)
ui.bar.entry.grab_focus()
ui.bar.child_focus(Gtk.DirectionType.TAB_FORWARD)
pump()
assert inside(ui.bar.review)
ui.bar.review.set_focusable(False)
ui.bar.entry.grab_focus()
# An explicitly targeted button click remains supported despite Tab exclusion.
ui.bar.review.emit("clicked")
pump()
assert model.reviewed_approval is not None and ui.card.reviewing
assert not any(isinstance(body, s.ApprovalRespond) for body in sent)
ui.hide()
'''
    try:
        run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    finally:
        proc.terminate()
        proc.wait(5)
    assert run.returncode == 0, run.stdout + run.stderr

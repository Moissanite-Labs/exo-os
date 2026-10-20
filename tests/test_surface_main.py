"""`exo-surface`'s entry point: the two paths a real login takes, with no display and no GTK.

main() either hands the command to a surface that already runs (through D-Bus) or re-executes itself once with
gtk4-layer-shell preloaded. Neither needs a display, so these tests load a private copy of app.py against a
stand-in for PyGObject. They run the same on a machine with and without GTK, and nothing they do reaches a
real bus or replaces the test process.
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

import exo_os.surface

APP = Path(exo_os.surface.__file__).with_name("app.py")
LIB = "libgtk4-layer-shell.so.0"


class Exec(Exception):
    """Raised by the fake os.execve, which never returns in a real process."""


class StubGio:
    class ApplicationFlags:
        HANDLES_COMMAND_LINE = 8

    class Application:
        instances: list = []

        def __init__(self, application_id=None, flags=None):
            self.application_id, self.flags, self.ran_with = application_id, flags, None
            StubGio.Application.instances.append(self)

        def run(self, argv):
            self.ran_with = list(argv)
            return 42


@pytest.fixture
def app(monkeypatch):
    gi = types.ModuleType("gi")
    gi.require_version = lambda *args: None
    repo = types.ModuleType("gi.repository")
    repo.Gio, repo.GLib = StubGio, types.SimpleNamespace(Error=Exception)
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    spec = importlib.util.spec_from_file_location("exo_os.surface._app_under_test", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    StubGio.Application.instances = []

    class Surface:  # the real one needs GTK
        created, ran_with = 0, None

        def __init__(self):
            Surface.created += 1

        def run(self, argv):
            Surface.ran_with = list(argv)
            return 7

    def no_exec(*args):
        raise Exec(*args)

    monkeypatch.setattr(module, "Surface", Surface)
    module.applied = []
    monkeypatch.setattr(module.renderer, "apply", lambda env: module.applied.append(env))
    monkeypatch.setattr(module.os, "execve", no_exec)
    for name in ("WAYLAND_DISPLAY", "GDK_BACKEND", "LD_PRELOAD", module.PRELOAD_MARK):
        monkeypatch.delenv(name, raising=False)
    return module


def test_command_goes_to_the_running_surface_without_starting_another(app, monkeypatch):
    asked = []
    monkeypatch.setattr(app, "name_has_owner", lambda name: asked.append(name) or True)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: pytest.fail("a forwarded command must not look at GTK"))
    argv = ["exo-surface", "toggle"]
    assert app.main(argv) == 42  # the running instance's exit code
    assert asked == [app.APP_ID]
    [remote] = StubGio.Application.instances
    assert remote.application_id == app.APP_ID
    assert remote.flags == StubGio.ApplicationFlags.HANDLES_COMMAND_LINE
    assert remote.ran_with == argv  # the whole command line, so the owner sees "toggle"
    assert app.Surface.created == 0


@pytest.mark.parametrize("cmd", ["quit", "hide"])
def test_quit_and_hide_with_no_surface_running_do_nothing(app, monkeypatch, cmd):
    monkeypatch.setattr(app, "name_has_owner", lambda name: False)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: pytest.fail("nothing runs, so there is nothing to start"))
    assert app.main(["exo-surface", cmd]) == 0
    assert StubGio.Application.instances == [] and app.Surface.created == 0


@pytest.mark.parametrize("cmd", ["toggle", "show"])
def test_show_with_no_surface_running_starts_one(app, monkeypatch, cmd):
    monkeypatch.setattr(app, "name_has_owner", lambda name: False)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: None)
    argv = ["exo-surface", cmd]
    assert app.main(argv) == 7
    assert app.Surface.ran_with == argv and StubGio.Application.instances == []


def test_first_run_reexecutes_once_with_layer_shell_preloaded(app, monkeypatch):
    monkeypatch.setattr(app, "name_has_owner", lambda name: False)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: LIB)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("LD_PRELOAD", "libfoo.so")
    with pytest.raises(Exec) as ran:
        app.main(["exo-surface", "show"])
    exe, argv, env = ran.value.args
    assert exe == sys.executable
    assert argv == [sys.executable, "-m", "exo_os.surface.app", "show"]  # the command survives the re-exec
    assert env["LD_PRELOAD"] == f"{LIB}:libfoo.so"  # the library comes first, the old value is kept
    assert env[app.PRELOAD_MARK] == "1"
    assert app.Surface.created == 0


@pytest.mark.parametrize("preload, mark", [
    ("libfoo.so", "1"),            # only the mark says this is the second run
    (f"{LIB}:libfoo.so", None),    # only the library says it is already loaded
])
def test_second_run_does_not_reexecute_again(app, monkeypatch, preload, mark):
    """Each guard alone must stop the exec; without them the surface would re-execute itself forever."""
    monkeypatch.setattr(app, "name_has_owner", lambda name: False)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: LIB)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("LD_PRELOAD", preload)
    if mark:
        monkeypatch.setenv(app.PRELOAD_MARK, mark)
    assert app.main(["exo-surface"]) == 7  # os.execve here would raise Exec
    assert app.Surface.ran_with == ["exo-surface"]


@pytest.mark.parametrize("lib, wayland", [(None, "wayland-1"), (LIB, None)])
def test_no_reexec_without_the_library_or_a_wayland_display(app, monkeypatch, lib, wayland):
    monkeypatch.setattr(app, "name_has_owner", lambda name: False)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: lib)
    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", wayland)
    assert app.main(["exo-surface"]) == 7


def test_renderer_policy_runs_in_the_primary_instance_before_gtk(app, monkeypatch):
    monkeypatch.setattr(app, "name_has_owner", lambda name: False)
    monkeypatch.setattr(app, "layer_shell_lib", lambda: None)
    order = []
    monkeypatch.setattr(app.renderer, "apply", lambda env: order.append("policy"))
    app.Surface.run = lambda self, argv: order.append("run") or 7
    assert app.main(["exo-surface"]) == 7 and order == ["policy", "run"]


def test_a_toggle_to_a_running_surface_never_probes(app, monkeypatch):
    monkeypatch.setattr(app, "name_has_owner", lambda name: True)
    app.main(["exo-surface", "toggle"])
    assert app.applied == []

"""`exo-surface`: runs the desktop surface, one instance per login (DESIGN.md §7).

    exo-surface            run the surface (started at login; the bar stays hidden until the key)
    exo-surface toggle     show or hide the bar of the running surface (bound to the prompt key)
    exo-surface show|hide|quit

A later `exo-surface toggle` finds the running instance on D-Bus and hands it the command line through
GApplication. That path loads only Gio, not GTK or the protocol, so the key answers quickly; the windows
live in `ui`, imported only by the instance that draws them.

gtk4-layer-shell and LD_PRELOAD: the library must be loaded before libwayland-client, or GTK opens a
normal window instead of a layer surface. Python loads libwayland when it first imports Gtk, too late to
fix in-process, so the first instance re-executes itself once with LD_PRELOAD=libgtk4-layer-shell.so.0
(EXO_SURFACE_PRELOADED marks the second run). Where layer-shell is not available (X11, the broadway
display the tests use) the same three shapes are plain undecorated windows.

To look at it by hand, use the Exo OS VM, not a working desktop: there `exo-surface.service` runs the
surface, and `exo-surface toggle` (or the prompt key) shows the bar.
"""
from __future__ import annotations

import ctypes.util
import os
import sys
from pathlib import Path
from typing import Any

import gi
from gi.repository import Gio, GLib

from .. import paths
from ..profile import Profile

APP_ID = "org.exo_os.Surface"
PRELOAD_MARK = "EXO_SURFACE_PRELOADED"
COMMANDS = ("toggle", "show", "hide", "quit")


def layer_shell_lib() -> str | None:
    return ctypes.util.find_library("gtk4-layer-shell")


def needs_preload(env: dict[str, str], lib: str | None) -> bool:
    """True when this process would open a Wayland display without the layer-shell library preloaded."""
    if not lib or env.get(PRELOAD_MARK) or "gtk4-layer-shell" in env.get("LD_PRELOAD", ""):
        return False
    if env.get("GDK_BACKEND", "wayland").split(",")[0] not in ("wayland", "*"):
        return False
    return bool(env.get("WAYLAND_DISPLAY"))


def preload_env(env: dict[str, str], lib: str) -> dict[str, str]:
    old = env.get("LD_PRELOAD", "")
    return {**env, PRELOAD_MARK: "1", "LD_PRELOAD": f"{lib}:{old}" if old else lib}


def load_profile(path: Path | None = None) -> Profile:
    """The machine profile from the machine repo, or the default one: a missing or broken file must not
    stop the bar from opening. Read again each time the bar opens, so a new app is offered at once."""
    path = path or paths.machine_repo() / "machine.toml"
    try:
        return Profile.loads(path.read_text())
    except (OSError, ValueError):
        return Profile.default()


def name_has_owner(name: str) -> bool:
    """Is a surface already running? Asked on D-Bus directly, so a toggle never initialises GTK."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
                              Gio.DBusCallFlags.NONE, 1000, None)
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False


class Surface:
    """The primary instance: a Gtk.Application that holds the windows and the link to exod."""

    def __init__(self) -> None:
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        self.gtk = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.gtk.connect("startup", self._startup)
        self.gtk.connect("command-line", self._command_line)
        self.gtk.connect("shutdown", self._shutdown)
        self.ui: Any = None
        self.conn: Any = None
        self._quiet: tuple[type, ...] = ()

    def run(self, argv: list[str]) -> int:
        return self.gtk.run(argv)

    def _startup(self, app: Any) -> None:
        from exo.protocol import schema as s

        from .conn import Connection
        from .model import SurfaceModel
        from .ui import SurfaceUI

        app.hold()  # live with no window shown: the bar is hidden most of the time
        self._quiet = (s.UiRespond,)  # panel signals: a refusal is not the person's problem
        self.ui = SurfaceUI(SurfaceModel(load_profile()), self._send, app=app)
        self.conn = Connection(self.ui)
        self.conn.start()

    def _send(self, body: Any) -> None:
        if self.conn is not None:
            self.conn.send(body, quiet=isinstance(body, self._quiet))

    def _command_line(self, app: Any, cmdline: Gio.ApplicationCommandLine) -> int:
        args = cmdline.get_arguments()[1:]
        cmd = args[0] if args else ""
        if cmd == "toggle":
            self.ui.hide() if self.ui.bar.get_visible() else self.ui.show(load_profile())
        elif cmd == "show":
            self.ui.show(load_profile())
        elif cmd == "hide":
            self.ui.hide()
        elif cmd == "quit":
            app.quit()
        return 0

    def _shutdown(self, app: Any) -> None:
        if self.conn is not None:
            self.conn.stop()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd in ("-h", "--help"):
        print(__doc__.split("\n\n")[1])
        return 0
    if cmd and cmd not in COMMANDS:
        print(f"exo-surface: unknown command {cmd!r}; use {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    if cmd and name_has_owner(APP_ID):
        # A surface runs: pass it the command line through GApplication without loading a display.
        remote = Gio.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        return remote.run(argv)
    if cmd in ("quit", "hide"):
        return 0  # nothing runs, nothing to hide
    lib = layer_shell_lib()
    if lib and needs_preload(dict(os.environ), lib):
        os.execve(sys.executable, [sys.executable, "-m", "exo_os.surface.app", *argv[1:]],
                  preload_env(dict(os.environ), lib))
    return Surface().run(argv)


if __name__ == "__main__":
    sys.exit(main())

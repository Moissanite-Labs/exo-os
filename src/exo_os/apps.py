"""Open or bring forward an app from the machine profile (door 3 in DESIGN.md §4.1).

If a window of the app is already open, focus it; otherwise start it. Web apps run as Chromium
`--app` windows, so each gets its own window class and workspace rule.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from typing import Any

from .profile import Profile


def launch_argv(app: dict[str, Any], profile: Profile) -> list[str]:
    from .sandbox.desktop import COMMANDS, check_app, required

    strict = required()
    if strict:
        check_app(app)
    if app.get("url"):
        scale = profile.get("display.text_scale")
        argv = ["/usr/bin/chromium" if strict else os.environ.get("EXO_OS_BROWSER", "chromium"), f"--app={app['url']}"]
        if scale != 1.0:
            argv.append(f"--force-device-scale-factor={scale:g}")
        return argv
    return [COMMANDS[app["command"]]] if strict else shlex.split(app["command"])


def find_window(cls: str) -> str | None:
    if not shutil.which("hyprctl"):
        return None
    r = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        return None
    try:
        clients = json.loads(r.stdout)
    except ValueError:
        return None
    return next((c["address"] for c in clients if c.get("class") == cls), None)


def focus(address: str) -> bool:
    # hyprctl dispatch takes a dispatcher name; the Lua hl.dsp.focus call is config-side only.
    r = subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{address}"], capture_output=True,
                       text=True, timeout=10)
    return r.returncode == 0


def open_app(profile: Profile, name: str, *, background: bool = False) -> str:
    """Returns one plain line saying what happened. Raises KeyError for an unknown app."""
    app = profile.app(name)
    if app is None:
        raise KeyError(name)
    argv = launch_argv(app, profile)  # validate before focus as well as before spawn
    address = find_window(app["class"]) if app.get("class") else None
    if address:
        if background:
            return f"{app['name']} is already open."
        focus(address)
        return f"Brought {app['name']} to the front."
    from .sandbox.desktop import required

    if required():
        # A project-local config/helper must never turn a desktop launch into host execution.
        env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "DISPLAY", "WAYLAND_DISPLAY",
               "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME") if key in os.environ}
        env["PATH"] = "/usr/bin:/bin"
        options = {"cwd": "/", "env": env}
    else:
        options = {}
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, **options)
    return f"Opened {app['name']}."

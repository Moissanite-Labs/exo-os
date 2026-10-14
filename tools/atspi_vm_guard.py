"""Refuse AT-SPI diagnostics outside the authenticated disposable Exo OS VM."""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path


def require_vm(*, opt_in: bool, fixture: bool = False) -> None:
    if not opt_in or os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1":
        raise ValueError("requires --vm-acceptance and EXO_OS_VM_ACCEPTANCE=1")
    if (sys.platform != "linux" or not Path("/run/systemd/system").is_dir()
            or os.geteuid() == 0):
        raise ValueError("requires a disposable Linux systemd VM as non-root exo")
    try:
        username = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:
        raise ValueError("requires the exo account") from exc
    if username != "exo":
        raise ValueError("requires the exo account")
    if (os.environ.get("GDK_BACKEND") != "wayland"
            or not os.environ.get("WAYLAND_DISPLAY")
            or not os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
        raise ValueError("requires the Exo Wayland display and user D-Bus session")
    if fixture and "gtk4-layer-shell" not in os.environ.get("LD_PRELOAD", ""):
        raise ValueError("requires gtk4-layer-shell preloaded before GTK")
    if os.environ.get("GSK_RENDERER") != "cairo":
        raise ValueError("requires the declared vmtest GSK_RENDERER=cairo")

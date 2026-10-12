"""Checks run after a change is written. If one fails, the change engine puts everything back.

A check gets the config folder the files were written to and returns (ok, message). Messages are
for the ledger and for Ahraz; the person only ever sees "That did not work, so I put it back."
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

Check = Callable[[Path], tuple[bool, str]]


def hyprland_config(config_home: Path) -> tuple[bool, str]:
    """`Hyprland --verify-config` on the generated file, loaded the way the real config loads it."""
    if not shutil.which("Hyprland"):
        return True, "Hyprland not installed; config not checked"
    profile = config_home / "hypr" / "exo" / "profile.lua"
    if not profile.exists():
        return True, "no generated Hyprland config"
    with tempfile.TemporaryDirectory() as tmp:
        wrapper = Path(tmp) / "hyprland.lua"
        wrapper.write_text(f"dofile({_lua(str(profile))})\n")
        r = subprocess.run(["Hyprland", "--verify-config", "-c", str(wrapper)],
                           capture_output=True, text=True, timeout=30, cwd=tmp)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0 and "config ok" in out:
        return True, "Hyprland config ok"
    return False, "Hyprland config check failed: " + out[-600:]


def hyprland_live(config_home: Path) -> tuple[bool, str]:
    """When a Hyprland session is running, reload it and check it still answers."""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not shutil.which("hyprctl"):
        return True, "no running Hyprland session"
    r = subprocess.run(["hyprctl", "reload"], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return False, "hyprctl reload failed: " + (r.stdout + r.stderr)[-300:]
    r = subprocess.run(["hyprctl", "-j", "configerrors"], capture_output=True, text=True, timeout=15)
    errors = config_errors(r.stdout) if r.returncode == 0 else []
    if errors:
        return False, "Hyprland reports config errors: " + "; ".join(errors)[-600:]
    r = subprocess.run(["hyprctl", "version"], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return False, "Hyprland stopped answering after reload"
    return True, "Hyprland reloaded"


def config_errors(text: str) -> list[str]:
    """`hyprctl -j configerrors` prints a list of strings; with no errors it is [""] (Hyprland 0.56)."""
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return [text.strip()] if text.strip() else []
    return [str(e).strip() for e in items if str(e).strip()]


def hypridle_restart(config_home: Path) -> tuple[bool, str]:
    """hypridle reads its file only at start; restart it if it runs as a user service."""
    if not shutil.which("systemctl"):
        return True, "no systemctl"
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "hypridle.service"]).returncode == 0
    if not active:
        return True, "hypridle not running as a service"
    r = subprocess.run(["systemctl", "--user", "restart", "hypridle.service"], capture_output=True, text=True,
                       timeout=30)
    if r.returncode != 0:
        return False, "hypridle failed to restart: " + r.stderr[-300:]
    return True, "hypridle restarted"


DEFAULT_CHECKS: list[Check] = [hyprland_config, hyprland_live, hypridle_restart]


def _lua(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

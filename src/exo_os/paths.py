"""Where Exo OS keeps things. Every path can be moved with an environment variable.

On an Exo OS image, EXO_HOME is /var/lib/exo/<user> (the @store btrfs subvolume, kept out of system
rollbacks); elsewhere it is Exo's default, ~/.exo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def exo_home() -> Path:
    return Path(os.environ.get("EXO_HOME") or Path.home() / ".exo")


def store() -> Path | None:
    """The principal store, found the way Exo's gate finds it (env, then config.json's principal)."""
    if os.environ.get("EXO_PRINCIPAL_STORE"):
        return Path(os.environ["EXO_PRINCIPAL_STORE"])
    principal = os.environ.get("EXO_PRINCIPAL")
    if not principal:
        try:
            principal = json.loads((exo_home() / "config.json").read_text()).get("principal")
        except (OSError, ValueError):
            principal = None
    if principal:
        path = exo_home() / "principals" / principal
        return path if path.is_dir() else None
    return None


def machine_repo() -> Path:
    if os.environ.get("EXO_OS_MACHINE"):
        return Path(os.environ["EXO_OS_MACHINE"])
    s = store()
    return (s / "machine") if s else exo_home() / "machine"


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


DESKTOP_OWNER = Path("hypr") / "exo" / "desktop-owner"
DESKTOP_MARK = "exo/profile.lua"
DESKTOP_OWNER_CONTENT = "exo-os-v1\n"
DESKTOP_INCLUDE = "dofile(generated)"


def owns_desktop(config: Path) -> bool:
    """True only for a marked Exo OS desktop that actively loads its generated profile.

    On any other desktop (Omarchy, a developer's own) Exo OS must not write config or reload Hyprland.
    """
    try:
        marker = (Path(config) / DESKTOP_OWNER).read_bytes()
        if marker != DESKTOP_OWNER_CONTENT.encode("utf-8"):
            return False
        source = (Path(config) / "hypr" / "hyprland.lua").read_text(encoding="utf-8")
        return any(line.strip() == DESKTOP_INCLUDE for line in source.splitlines())
    except (OSError, UnicodeError):
        return False


def state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "exo-os"

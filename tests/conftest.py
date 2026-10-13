import shutil
import tempfile
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "image/mkosi.extra/etc/skel/.config/hypr/hyprland.lua"
OWNER = Path(__file__).resolve().parent.parent / "image/mkosi.extra/etc/skel/.config/hypr/exo/desktop-owner"


def make_exo_desktop(config: Path) -> Path:
    """Give a config folder the Exo OS base Hyprland config, the one the image ships."""
    (config / "hypr").mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE, config / "hypr" / "hyprland.lua")
    (config / "hypr" / "exo").mkdir(parents=True, exist_ok=True)
    shutil.copy(OWNER, config / "hypr" / "exo" / "desktop-owner")
    return config


@pytest.fixture
def exo_desktop(tmp_path):
    return make_exo_desktop(tmp_path / "config")


@pytest.fixture
def socket_dir():
    """Short per-test path for Unix sockets, which have a small AF_UNIX path limit."""
    with tempfile.TemporaryDirectory(prefix="exo-", dir="/tmp") as tmp:
        yield Path(tmp)

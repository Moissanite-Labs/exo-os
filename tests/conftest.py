import shutil
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "image/mkosi.extra/etc/skel/.config/hypr/hyprland.lua"


def make_exo_desktop(config: Path) -> Path:
    """Give a config folder the Exo OS base Hyprland config, the one the image ships."""
    (config / "hypr").mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE, config / "hypr" / "hyprland.lua")
    return config


@pytest.fixture
def exo_desktop(tmp_path):
    return make_exo_desktop(tmp_path / "config")

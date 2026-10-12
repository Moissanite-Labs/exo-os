import shutil
import subprocess

import pytest

from exo_os.profile import Profile
from exo_os.render.hyprland import class_pattern, hypridle, lua_str, render


def _profile():
    return (Profile.default()
            .put_app({"name": "WhatsApp", "url": "https://web.whatsapp.com/", "key": "SUPER+1", "workspace": 2,
                      "autostart": True})
            .set_value("keyboard.layouts", ["us", "ara", "pk"]))


def test_lua_strings_are_escaped():
    assert lua_str('a"b\\c\nd') == '"a\\"b\\\\c\\nd"'


def test_class_pattern_is_anchored_and_escaped():
    assert class_pattern("chrome-web.whatsapp.com__-Default") == "^chrome-web\\.whatsapp\\.com__-Default$"


def test_render_contents():
    files = render(_profile())
    lua = files["hypr/exo/profile.lua"]
    assert 'kb_layout = "us,ara,pk"' in lua
    assert 'hl.bind("SUPER + 1", hl.dsp.exec_cmd("exo-os app open \'WhatsApp\'")' in lua
    assert 'workspace = "2 silent"' in lua
    assert "exo-os app open 'WhatsApp' --background" in lua
    assert 'hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("exo-surface toggle")' in lua


def test_hypridle_zero_means_never():
    p = Profile.default().set_value("idle.lock_seconds", 0).set_value("idle.dim_seconds", 0)
    assert "listener" not in hypridle(p)
    assert "timeout = 600" in hypridle(Profile.default())


@pytest.mark.skipif(not shutil.which("Hyprland"), reason="needs Hyprland")
def test_hyprland_accepts_rendered_config(tmp_path):
    (tmp_path / "profile.lua").write_text(render(_profile())["hypr/exo/profile.lua"])
    (tmp_path / "hyprland.lua").write_text(f'dofile("{tmp_path}/profile.lua")\n')
    r = subprocess.run(["Hyprland", "--verify-config", "-c", str(tmp_path / "hyprland.lua")],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and "config ok" in r.stdout + r.stderr

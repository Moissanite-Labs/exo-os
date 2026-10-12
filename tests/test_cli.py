import pytest

from exo_os import cli

from conftest import make_exo_desktop


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(make_exo_desktop(tmp_path / "config")))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.delenv("EXO_PRINCIPAL", raising=False)
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setattr("exo_os.checks.DEFAULT_CHECKS", [])
    monkeypatch.setattr("exo_os.changes.DEFAULT_CHECKS", [])
    return tmp_path


def test_parse_value():
    assert cli.parse_value("display.scale", "125%") == 1.25
    assert cli.parse_value("keyboard.layouts", "us, ara,pk") == ["us", "ara", "pk"]
    assert cli.parse_value("pointer.natural_scroll", "on") is True
    assert cli.parse_value("idle.lock_seconds", "3600") == 3600


def test_set_history_undo(env, capsys):
    assert cli.main(["init"]) == 0
    assert cli.main(["set", "keyboard.layouts", "us,ara,pk"]) == 0
    assert "Keyboard languages: English, Arabic, Urdu. Undo?" in capsys.readouterr().out
    assert cli.main(["set", "idle.lock_seconds", "3600"]) == 0
    assert "Lock the screen after: 1 hour" in capsys.readouterr().out
    assert cli.main(["set", "display.scale", "9"]) == 2
    assert "Not changed: display.scale must be between" in capsys.readouterr().err
    cli.main(["history"])
    out = capsys.readouterr().out.splitlines()
    assert "Lock the screen after: 1 hour" in out[0] and "(person)" in out[0]
    assert cli.main(["undo"]) == 0
    capsys.readouterr()
    cli.main(["show", "idle.lock_seconds"])
    assert capsys.readouterr().out.strip() == "1800"


def test_app_add_remove(env, capsys):
    cli.main(["init"])
    assert cli.main(["app", "add", "Gmail", "--url", "https://mail.google.com/", "--key", "SUPER+2",
                     "--workspace", "3"]) == 0
    assert "Added Gmail on SUPER+2" in capsys.readouterr().out
    lua = (env / "config/hypr/exo/profile.lua").read_text()
    assert "chrome-mail\\\\.google\\\\.com__-Default" in lua
    assert cli.main(["app", "remove", "Gmail"]) == 0
    assert cli.main(["app", "open", "Nope"]) == 1

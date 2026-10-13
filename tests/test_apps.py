"""apps.py had no tests: launching, window lookup, and focusing."""
import json

from exo_os import apps
from exo_os.profile import Profile


def _profile():
    return Profile.default()


def test_web_app_launches_chromium_app_window():
    prof = _profile()
    argv = apps.launch_argv({"name": "Mail", "url": "https://mail.example.com/"}, prof)
    assert argv[:2] == ["chromium", "--app=https://mail.example.com/"]


def test_text_scale_is_passed_to_chromium():
    prof = _profile().set_value("display.text_scale", 1.5)
    argv = apps.launch_argv({"name": "Mail", "url": "https://mail.example.com/"}, prof)
    assert "--force-device-scale-factor=1.5" in argv


def test_command_app_is_split_without_a_shell():
    assert apps.launch_argv({"name": "T", "command": "foot --title 'a b'"}, _profile()) == [
        "foot", "--title", "a b"]


def test_find_window_matches_exact_class(monkeypatch):
    clients = [{"address": "0x1", "class": "chromium"}, {"address": "0x2", "class": "chromiumx"}]
    monkeypatch.setattr(apps.shutil, "which", lambda *a, **k: "/usr/bin/hyprctl")
    monkeypatch.setattr(apps.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": json.dumps(clients)})())
    assert apps.find_window("chromium") == "0x1"


def test_find_window_returns_none_without_hyprctl(monkeypatch):
    monkeypatch.setattr(apps.shutil, "which", lambda *a, **k: None)
    assert apps.find_window("chromium") is None


def test_focus_uses_the_focuswindow_dispatcher(monkeypatch):
    seen = []
    monkeypatch.setattr(apps.subprocess, "run",
                        lambda argv, **k: seen.append(argv) or type("R", (), {"returncode": 0})())
    assert apps.focus("0xabc") is True
    assert seen == [["hyprctl", "dispatch", "focuswindow", "address:0xabc"]]

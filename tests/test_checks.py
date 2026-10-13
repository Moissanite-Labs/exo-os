from exo_os import checks
from exo_os.profile import Profile


def test_keyboard_layout_check_reports_missing_database_as_unchecked(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "XKB", tmp_path / "missing-xkb")
    monkeypatch.setattr(checks.shutil, "which", lambda _name: None)

    ok, message = checks.keyboard_layouts(tmp_path, Profile.default())

    assert ok
    assert "database unavailable" in message
    assert "not checked" in message
    assert "ok" not in message


def test_keyboard_layout_check_uses_isolated_symbols_database(tmp_path, monkeypatch):
    symbols = tmp_path / "xkb" / "symbols"
    symbols.mkdir(parents=True)
    (symbols / "us").touch()
    (symbols / "ara").touch()
    monkeypatch.setattr(checks, "XKB", symbols.parent)
    monkeypatch.setattr(checks.shutil, "which", lambda _name: None)

    profile = Profile.default().set_value("keyboard.layouts", ["us", "ara"])
    assert checks.keyboard_layouts(tmp_path, profile) == (
        True, "keyboard layouts found in XKB symbols; compiler unavailable"
    )

    unknown = profile.set_value("keyboard.layouts", ["us", "zz"])
    ok, message = checks.keyboard_layouts(tmp_path, unknown)
    assert not ok and "zz" in message

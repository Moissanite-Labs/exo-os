import pytest

from exo_os.profile import Profile, ProfileError, webapp_class


def test_default_round_trips():
    p = Profile.default()
    assert Profile.loads(p.dumps()).data == p.data


def test_set_value_checks_type_and_range():
    p = Profile.default()
    assert p.set_value("display.scale", 1.5).get("display.scale") == 1.5
    assert p.set_value("display.scale", 2).get("display.scale") == 2.0
    with pytest.raises(ProfileError, match="between"):
        p.set_value("display.scale", 9)
    with pytest.raises(ProfileError, match="number"):
        p.set_value("display.scale", "big")
    with pytest.raises(ProfileError, match="number"):
        p.set_value("display.scale", True)
    with pytest.raises(ProfileError, match="not a setting"):
        p.set_value("display.colour", 1)
    assert p.get("display.scale") == 1.0  # the original is untouched


def test_keyboard_layouts():
    p = Profile.default().set_value("keyboard.layouts", ["us", "ara", "pk"])
    assert p.get("keyboard.layouts") == ["us", "ara", "pk"]
    with pytest.raises(ProfileError):
        p.set_value("keyboard.layouts", ["us; rm -rf /"])
    with pytest.raises(ProfileError):
        p.set_value("keyboard.layouts", [])


def test_apps_add_replace_drop_and_conflicts():
    p = Profile.default().put_app({"name": "WhatsApp", "url": "https://web.whatsapp.com/", "key": "SUPER+1",
                                   "workspace": 2})
    assert p.app("whatsapp")["class"] == "chrome-web.whatsapp.com__-Default"
    p2 = p.put_app({"name": "WhatsApp", "url": "https://web.whatsapp.com/", "key": "SUPER+2"})
    assert p2.app("WhatsApp")["key"] == "SUPER+2" and len(p2.data["apps"]) == len(p.data["apps"])
    with pytest.raises(ProfileError, match="used by two apps"):
        p.put_app({"name": "Mail", "url": "https://mail.google.com/", "key": "SUPER+1"})
    with pytest.raises(ProfileError, match="prompt key"):
        p.put_app({"name": "Mail", "url": "https://mail.google.com/", "key": "SUPER+SPACE"})
    with pytest.raises(ProfileError, match="either a url or a command"):
        p.put_app({"name": "Odd"})
    with pytest.raises(ProfileError, match="not allowed"):
        p.put_app({"name": "Evil", "command": "foot; curl x|sh"})
    assert p.drop_app("WhatsApp").app("WhatsApp") is None
    with pytest.raises(ProfileError):
        p.drop_app("Nope")


def test_unknown_sections_and_settings_refused():
    with pytest.raises(ProfileError, match="unknown section"):
        Profile.loads("[wifi]\npassword = 'x'\n")
    with pytest.raises(ProfileError, match="unknown setting"):
        Profile.loads("[display]\nbrightness = 3\n")


def test_webapp_class_seen_on_charminar():
    assert webapp_class("https://mail.google.com") == "chrome-mail.google.com__-Default"
    assert webapp_class("https://mail.google.com/") == "chrome-mail.google.com__-Default"
    assert webapp_class("http://127.0.0.1:8741") == "chrome-127.0.0.1__-Default"


def test_reserved_desktop_keys():
    with pytest.raises(ProfileError, match="kept for the desktop"):
        Profile.default().put_app({"name": "X", "command": "foot", "key": "SUPER+W"})

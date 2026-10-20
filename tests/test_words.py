from exo_os.words import describe_call, plain_summary, plain_wording


def test_describe_exo_os_calls():
    assert describe_call("system_package_install", {"names": ["htop"], "reason": "so you can see memory use"}) == \
        "Install htop on this computer? Reason: so you can see memory use. " \
        "This refreshes the package lists and upgrades all system packages first. It can be undone."
    assert describe_call("machine_set", {"setting": "display.scale", "value": 1.25}) == "Change screen size to 125%?"
    assert describe_call("send_message", {"target": "Ahmed", "message": "Good morning"}) == \
        "Send this message to Ahmed? “Good morning”"
    assert describe_call("mystery_tool", {}) is None


def test_plain_summary_drops_gate_jargon():
    s = "system_package_install (costly, private). Why: installing software changes the system. Default if no reply: skip."
    assert plain_summary(s) == "Installing software changes the system."
    assert plain_summary("") == "Exo is waiting for your answer."


import pytest

MALFORMED = [
    ("system_package_install", {"names": [1, 2], "reason": "x"}),
    ("system_package_install", {"names": "git"}),  # would read "Install g, i, t"
    ("system_package_install", {"names": []}),
    ("system_package_install", {"names": ["git", None]}),
    ("system_package_install", {"names": ["git"], "reason": {"a": 1}}),
    ("system_package_install", {}),
    ("system_snapshot_undo", {"post": ["x"]}),
    ("system_snapshot_undo", {}),
    ("machine_set", {"setting": ["display.scale"], "value": 1}),
    ("machine_set", {"setting": "display.scale", "value": "big"}),
    ("machine_set", {"setting": "display.scale", "value": float("inf")}),
    ("machine_set", {"setting": "display.text_scale", "value": [1]}),
    ("machine_set", {"setting": "idle.lock_seconds", "value": "60"}),
    ("machine_set", {"setting": "idle.lock_seconds", "value": False}),  # would read "never"
    ("machine_set", {"setting": "idle.lock_seconds", "value": None}),
    ("machine_set", {"setting": "keyboard.layouts", "value": [1, 2]}),
    ("machine_set", {"setting": "keyboard.layouts", "value": "us"}),
    ("machine_set", {"setting": "look.theme", "value": {"a": 1}}),
    ("machine_app_put", {"name": ["x"]}),
    ("app_open", {"name": 5}),
    ("send_message", {"target": {"a": 1}, "message": "hi"}),
    ("bash", {"command": ["ls"]}),
    ("write_file", {"path": 7}),
    ("bash", ["not", "a", "dict"]),
    ("bash", "ls"),
]


@pytest.mark.parametrize("name,args", MALFORMED)
def test_describe_call_never_raises_and_never_words_malformed_arguments(name, args):
    assert describe_call(name, args) is None
    assert plain_wording(name, args) == (None, True)


@pytest.mark.parametrize("name", ["system_package_install", "machine_set", "bash", "mystery_tool"])
@pytest.mark.parametrize("args", [None, [], "x", 5, [1, 2], {"names": {"a": 1}, "setting": {}, "value": object()}])
def test_describe_call_survives_any_argument_shape(name, args):
    describe_call(name, args)


def test_unknown_tools_and_settings_are_not_malformed():
    assert plain_wording("mystery_tool", {"x": 1}) == (None, False)
    assert plain_wording("machine_set", {"setting": "not.a.setting", "value": 1}) == (None, False)
    assert plain_wording("machine_set", {"setting": "idle.lock_seconds", "value": 0})[0] == \
        "Change lock the screen after to never?"


def test_install_card_always_discloses_the_system_upgrade():
    # sysd runs pacman -Sy then -Su, so an install upgrades every out-of-date package. The card must say so.
    from exo_os import plugin, setup
    from exo_os.sysd import actions

    card = describe_call("system_package_install", {"names": ["htop"]}).lower()
    assert "upgrades all system packages" in card and "refreshes the package lists" in card
    assert "undone" in card
    schema = {row[0]: row[3] for row in plugin.TOOLS}["system_package_install"]["description"].lower()
    assert "upgrades all system packages" in schema
    assert "upgrades all system packages" in setup.GATE_RULES
    import inspect
    assert '"-Su"' in inspect.getsource(actions.Actions.package_install)  # the claim above is still true

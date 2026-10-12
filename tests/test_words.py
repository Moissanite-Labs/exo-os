from exo_os.words import describe_call, plain_summary


def test_describe_exo_os_calls():
    assert describe_call("system_package_install", {"names": ["htop"], "reason": "so you can see memory use"}) == \
        "Install htop on this computer? Reason: so you can see memory use. It can be undone."
    assert describe_call("machine_set", {"setting": "display.scale", "value": 1.25}) == "Change screen size to 125%?"
    assert describe_call("send_message", {"target": "Ahmed", "message": "Good morning"}) == \
        "Send this message to Ahmed? “Good morning”"
    assert describe_call("mystery_tool", {}) is None


def test_plain_summary_drops_gate_jargon():
    s = "system_package_install (costly, private). Why: installing software changes the system. Default if no reply: skip."
    assert plain_summary(s) == "Installing software changes the system."
    assert plain_summary("") == "Exo is waiting for your answer."

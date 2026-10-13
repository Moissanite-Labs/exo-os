import json
import subprocess
import sys
import threading

import pytest
from conftest import make_exo_desktop

from exo_os.changes import NOT_OURS, ChangeEngine
from exo_os.ledger import ExoLedgerSink, MemorySink


def _engine(tmp_path, checks=None, ledger=None):
    return ChangeEngine(tmp_path / "store" / "machine", make_exo_desktop(tmp_path / "config"), ledger=ledger or MemorySink(),
                        checks=checks if checks is not None else [])


def test_init_apply_history_and_deploy(tmp_path):
    eng = _engine(tmp_path)
    assert eng.init().ok
    r = eng.apply(eng.profile().set_value("display.scale", 1.5), "Made everything larger", by="shahid")
    assert r.ok and r.change and r.message == "Made everything larger. Undo?"
    assert "scale = 1.5" in (tmp_path / "config/hypr/exo/profile.lua").read_text()
    assert "scale = 1.5" in (tmp_path / "store/machine/rendered/hypr/exo/profile.lua").read_text()
    assert "-scale = 1.0" in r.diff and "+scale = 1.5" in r.diff
    h = eng.history()
    assert [x["summary"] for x in h] == ["Made everything larger", "Set up the machine profile"]
    assert h[0]["by"] == "shahid"
    phases = [e["phase"] for e in eng.ledger.events]
    assert phases == ["change.proposed", "change.applied", "change.proposed", "change.applied"]


def test_no_op_change_does_nothing(tmp_path):
    eng = _engine(tmp_path)
    eng.init()
    r = eng.apply(eng.profile(), "Nothing")
    assert r.ok and r.change is None and len(eng.history()) == 1


def test_failed_check_reverts_everything(tmp_path):
    def picky(config_home, profile):
        text = (config_home / "hypr/exo/profile.lua").read_text()
        return ("scale = 2" not in text, "scale 2 breaks the screen")

    eng = _engine(tmp_path, checks=[picky])
    eng.init()
    before_cfg = (tmp_path / "config/hypr/exo/profile.lua").read_text()
    before_toml = (tmp_path / "store/machine/machine.toml").read_text()
    r = eng.apply(eng.profile().set_value("display.scale", 2.0), "Bigger")
    assert not r.ok and r.reverted and r.message == "That did not work, so I put it back."
    assert (tmp_path / "config/hypr/exo/profile.lua").read_text() == before_cfg
    assert (tmp_path / "store/machine/machine.toml").read_text() == before_toml
    assert len(eng.history()) == 1
    assert eng.ledger.events[-1]["phase"] == "change.reverted"
    assert "scale 2 breaks" in eng.ledger.events[-1]["check"]
    status = subprocess.run(["git", "-C", str(eng.repo), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""


def test_crashing_check_counts_as_failure(tmp_path):
    def boom(config_home, profile):
        raise RuntimeError("no display")

    eng = _engine(tmp_path)
    eng.init()
    eng.checks = [boom]
    r = eng.apply(eng.profile().set_value("look.gaps", 4), "Gaps")
    assert not r.ok and "crashed" in r.checks[-1]


def test_undo_is_a_logged_change_and_can_be_undone(tmp_path):
    eng = _engine(tmp_path)
    eng.init()
    eng.apply(eng.profile().set_value("keyboard.layouts", ["us", "ara"]), "Added Arabic keyboard")
    eng.apply(eng.profile().set_value("display.scale", 1.25), "Larger")
    r = eng.undo()
    assert r.ok and eng.profile().get("display.scale") == 1.0
    assert eng.profile().get("keyboard.layouts") == ["us", "ara"]
    assert eng.history()[0]["summary"] == "Undo: Larger"
    assert eng.history()[0]["undoes"] == eng.history()[1]["change"]
    arabic = eng.history()[2]["change"]
    assert eng.undo(arabic).ok and eng.profile().get("keyboard.layouts") == ["us"]
    assert eng.undo().ok and eng.profile().get("keyboard.layouts") == ["us", "ara"]  # undo the undo


def test_undo_with_nothing_to_undo(tmp_path):
    eng = _engine(tmp_path)
    assert not eng.undo().ok
    eng.init()
    assert not eng.undo().ok


def test_dropped_rendered_file_is_removed(tmp_path):
    files = {"a.conf": "1", "b.conf": "2"}
    eng = ChangeEngine(tmp_path / "m", make_exo_desktop(tmp_path / "c"), checks=[], renderer=lambda p: dict(files))
    eng.init()
    assert (tmp_path / "c/b.conf").exists()
    del files["b.conf"]
    eng.apply(eng.profile().set_value("look.gaps", 2), "drop b")
    assert not (tmp_path / "c/b.conf").exists() and not (tmp_path / "m/rendered/b.conf").exists()


def test_events_land_in_a_verifiable_exo_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    store = tmp_path / "home" / "principals" / "p"
    made = subprocess.run([sys.executable, "-m", "exo.cli", "init", "p", "--root", str(store)],
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    sink = ExoLedgerSink(store, tmp_path / "home" / "keys" / "exo-os.key")
    eng = ChangeEngine(store / "machine", make_exo_desktop(tmp_path / "config"), ledger=sink, checks=[])
    eng.init()
    eng.apply(eng.profile().set_value("display.scale", 1.5), "Larger")
    rows = [e for e in sink.ledger.iter(kind="action")]
    assert [r["body"]["phase"] for r in rows] == ["change.proposed", "change.applied"] * 2
    assert all(r["body"]["os"] == "exo-os" and r["author"] == "exo-os" for r in rows)
    out = subprocess.run([sys.executable, "-m", "exo.cli", "log", "verify", "--root", str(store)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads(out.stdout)["ok"] is True


def test_unknown_keyboard_layout_is_put_back(tmp_path, monkeypatch):
    from exo_os.checks import keyboard_layouts

    symbols = tmp_path / "xkb" / "symbols"
    symbols.mkdir(parents=True)
    for layout in ("us", "ara", "pk"):
        (symbols / layout).touch()
    monkeypatch.setattr("exo_os.checks.XKB", symbols.parent)
    monkeypatch.setattr("exo_os.checks.shutil.which", lambda name: None)
    eng = _engine(tmp_path, checks=[keyboard_layouts])
    eng.init()
    r = eng.apply(eng.profile().set_value("keyboard.layouts", ["us", "zz"]), "Bad layout")
    assert not r.ok and r.reverted and "zz" in r.checks[-1]
    assert eng.profile().get("keyboard.layouts") == ["us"]
    assert eng.apply(eng.profile().set_value("keyboard.layouts", ["us", "ara", "pk"]), "Good layouts").ok


def test_a_desktop_that_is_not_exo_os_is_left_alone(tmp_path):
    config = tmp_path / "config"
    (config / "hypr").mkdir(parents=True)
    (config / "hypr/hyprland.lua").write_text('require("looknfeel")\n')  # someone else's desktop
    ran, sink = [], MemorySink()
    eng = ChangeEngine(tmp_path / "store" / "machine", config, ledger=sink,
                       checks=[lambda c, p: ran.append(1) or (True, "ran")])
    for r in (eng.init(), eng.apply(eng.profile().set_value("display.scale", 1.5), "Larger"), eng.redeploy()):
        assert not r.ok and r.message == NOT_OURS
    assert sorted(p.name for p in config.rglob("*")) == ["hypr", "hyprland.lua"]
    assert not (tmp_path / "store").exists() and not ran and not sink.events


# -- failure paths of the engine itself (not of a check)

class _FailingLedger(MemorySink):
    """A ledger that cannot take one phase."""

    def __init__(self, phase):
        super().__init__()
        self.phase = phase

    def append(self, phase, body):
        if phase == self.phase:
            raise OSError("ledger unavailable")
        return super().append(phase, body)


def _phases(eng):
    return [e["phase"] for e in eng.ledger.events]


def test_git_failure_after_the_write_puts_the_change_back(tmp_path):
    eng = _engine(tmp_path)
    eng.init()
    lock = tmp_path / "store/machine/.git/index.lock"
    lock.write_text("")  # what a crash leaves behind
    deployed = tmp_path / "config/hypr/exo/profile.lua"
    before = deployed.read_text()
    new = eng.profile().set_value("keyboard.repeat_rate", 60)
    with pytest.raises(subprocess.CalledProcessError):
        eng.apply(new, "Repeat: 60")
    assert deployed.read_text() == before
    assert eng.profile().get("keyboard.repeat_rate") != 60
    assert _phases(eng)[-2:] == ["change.proposed", "change.reverted"]
    lock.unlink()
    r = eng.apply(new, "Repeat: 60")  # the retry is a real change, not "Nothing to change."
    assert r.ok and r.change
    assert [h["summary"] for h in eng.history()][0] == "Repeat: 60"
    assert eng.undo().ok


def test_ledger_failure_after_the_commit_takes_the_commit_back(tmp_path):
    eng = _engine(tmp_path, ledger=_FailingLedger("change.applied"))
    eng.ledger.phase = "never"
    eng.init()
    eng.ledger.phase = "change.applied"
    deployed = tmp_path / "config/hypr/exo/profile.lua"
    before = deployed.read_text()
    with pytest.raises(OSError):
        eng.apply(eng.profile().set_value("keyboard.repeat_rate", 60), "Repeat: 60")
    assert [h["summary"] for h in eng.history()] == ["Set up the machine profile"]
    assert deployed.read_text() == before
    assert eng.profile().get("keyboard.repeat_rate") != 60
    status = subprocess.run(["git", "-C", str(tmp_path / "store/machine"), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status == ""
    assert _phases(eng)[-2:] == ["change.proposed", "change.reverted"]


def test_failed_first_setup_after_the_write_leaves_no_trace(tmp_path):
    eng = _engine(tmp_path, ledger=_FailingLedger("change.applied"))
    with pytest.raises(OSError):
        eng.init()
    assert eng.history() == []
    assert not (tmp_path / "config/hypr/exo/profile.lua").exists()
    assert _phases(eng) == ["change.proposed", "change.reverted"]
    eng.ledger.phase = "never"
    assert eng.init().ok  # and setup can try again
    assert len(eng.history()) == 1


def _failing_first_init(tmp_path):
    eng = _engine(tmp_path, checks=[lambda c, p: (False, "boom") if p is not None else (True, "ok")])
    r = eng.init()
    assert not r.ok and r.reverted
    return eng


def test_history_and_undo_after_a_failed_first_init(tmp_path):
    eng = _failing_first_init(tmp_path)
    assert eng.history() == []
    r = eng.undo()
    assert not r.ok and r.message == "There is nothing to undo."


def test_setup_tries_the_first_init_again(tmp_path, monkeypatch):
    from exo_os import setup

    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(make_exo_desktop(tmp_path / "config")))
    monkeypatch.setenv("EXO_PRINCIPAL", "pat")
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.setattr("exo_os.changes.DEFAULT_CHECKS", [lambda c, p: (False, "boom") if p is not None else (True, "ok")])
    assert setup.main() == 0
    repo = tmp_path / "home/principals/pat/machine"
    assert (repo / ".git").exists() and ChangeEngine(repo, tmp_path / "config").history() == []
    monkeypatch.setattr("exo_os.changes.DEFAULT_CHECKS", [])
    assert setup.main() == 0
    assert len(ChangeEngine(repo, tmp_path / "config").history()) == 1


def test_undo_after_a_redeploy_undoes_the_last_real_change(tmp_path):
    eng = _engine(tmp_path)
    eng.init()
    eng.apply(eng.profile().set_value("look.theme", "dark"), "Theme: dark")
    assert eng.redeploy().ok
    # naming the redeploy itself still does what it says: nothing to put back
    redeploy = eng.history()[0]
    assert redeploy["summary"].startswith("Put the desktop config back")
    assert eng.undo(redeploy["change"]).message == "Nothing to change."
    r = eng.undo()
    assert r.ok and r.message == "Undo: Theme: dark. Undo?"
    assert eng.profile().get("look.theme") != "dark"


def test_undo_with_only_a_redeploy_after_setup_says_it_was_the_first_setup(tmp_path):
    eng = _engine(tmp_path)
    eng.init()
    eng.redeploy()
    r = eng.undo()
    assert not r.ok and "first setup" in r.message


def test_overlapping_changes_both_land(tmp_path):
    """The profile is read inside the lock, so a second change waits and builds on the first."""
    eng = _engine(tmp_path)
    eng.init()
    inside, release = threading.Event(), threading.Event()

    def slow(prof):
        inside.set()
        release.wait(5)
        return prof.set_value("look.theme", "dark"), "Theme: dark"

    first = threading.Thread(target=lambda: eng.change(slow))
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=lambda: eng.change(
        lambda prof: (prof.set_value("keyboard.repeat_rate", 50), "Repeat: 50")))
    second.start()
    release.set()
    first.join(10)
    second.join(10)
    p = eng.profile()
    assert p.get("look.theme") == "dark" and p.get("keyboard.repeat_rate") == 50
    assert [h["summary"] for h in eng.history()][:2] == ["Repeat: 50", "Theme: dark"]

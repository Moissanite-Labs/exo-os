import json
import subprocess
import sys

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


def test_a_desktop_without_the_owner_marker_is_refused_with_both_conditions_named(tmp_path):
    config = make_exo_desktop(tmp_path / "config")
    (config / "hypr/exo/desktop-owner").unlink()  # a machine built before the marker: the include is present
    result = ChangeEngine(tmp_path / "store" / "machine", config, ledger=MemorySink()).init()
    assert not result.ok and result.message == NOT_OURS
    text = " ".join(result.checks)
    assert "hypr/exo/desktop-owner" in text and "exo-os-v1" in text and "dofile(generated)" in text
    assert "does not load" not in text


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

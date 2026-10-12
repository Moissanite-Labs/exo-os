import datetime as dt
import subprocess
import sys

from exo import crypto
from exo.ledger import Ledger

from exo_os import timeline
from exo_os.changes import ChangeEngine
from exo_os.ledger import ExoLedgerSink

from conftest import make_exo_desktop


def test_a_day_in_plain_words(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    store = tmp_path / "home" / "principals" / "p"
    subprocess.run([sys.executable, "-m", "exo.cli", "init", "p", "--root", str(store)], check=True,
                   capture_output=True)
    sink = ExoLedgerSink(store, tmp_path / "home" / "keys" / "exo-os.key")
    eng = ChangeEngine(store / "machine", make_exo_desktop(tmp_path / "config"), ledger=sink, checks=[])
    eng.init(by="setup")
    eng.apply(eng.profile().set_value("display.scale", 1.25), "Screen size: 125%", by="person")
    eng.checks = [lambda c, p: (False, "no")]
    eng.apply(eng.profile().set_value("look.gaps", 8), "Space between windows: 8", by="exo (thread s_1)")
    gate = Ledger(store / "ledger.sqlite", crypto.Signer("exo-gate", crypto.keygen()))
    ask = gate.append("action", {"gate": "exo-gate", "phase": "decision", "tool": "system_package_install",
                                 "outcome": "ask", "classification": {"summary": "install htop"}})
    gate.append("action", {"gate": "exo-gate", "phase": "approval", "approved": True, "choice": "once"},
                refs=(ask["id"],))
    gate.append("action", {"gate": "exo-gate", "phase": "decision", "tool": "send_message", "outcome": "deny",
                           "classification": {"summary": "send message to a stranger"}})
    sink.append("system.action", {"receipt": {"action": "package.install", "ok": True, "args": {"names": ["htop"]},
                                              "result": {"pre": 3, "post": 4}}})
    today = dt.datetime.now().astimezone().date()
    rows = timeline.read(store, today)
    whats = [r["what"] for r in rows]
    assert whats == ["Screen size: 125%",
                     "Tried: Space between windows: 8. It did not work, so it was put back.",
                     "Asked you before: install htop. You said yes.",
                     "Blocked: send message to a stranger.",
                     "Installed htop."]
    assert rows[0]["by"] == "you" and rows[0]["undo"] and rows[-1]["undo"] == "system 3 4"
    text = timeline.say(rows, today, today)
    assert text.startswith("What happened today:") and ("AM" in text or "PM" in text)
    assert timeline.read(store, today - dt.timedelta(days=1)) == []
    assert timeline.say([], today - dt.timedelta(days=1), today) == "Exo did nothing yesterday."


def test_resolve_day():
    now = dt.datetime(2026, 10, 12, 9, 0).astimezone()
    assert timeline.resolve_day("yesterday", now) == dt.date(2026, 10, 11)
    assert timeline.resolve_day("2026-10-01", now) == dt.date(2026, 10, 1)

"""The exo-os plugin inside Exo's own engine, with Exo's gate loaded, driven by Exo's fake model."""
import asyncio
import json
from pathlib import Path

import pytest

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher


GATE = Path(__file__).resolve().parents[1] / ".deps" / "exo" / "plugins"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setattr("exo_os.changes.DEFAULT_CHECKS", [])
    return tmp_path


def _engine(tmp_path, rules):
    pm = PluginManager(config={"exo-gate": {"decision_model": False}})
    # Exo's own discovery: exo-gate from the pinned checkout's folder, exo-os from its entry point.
    loaded = pm.load_all(dirs=[GATE], enabled=["exo-gate", "exo-os"], entry_points=True)
    assert {p.manifest.name for p in loaded} >= {"exo-gate", "exo-os"}, [p.manifest.name for p in loaded]
    assert not pm.errors(), pm.errors()
    return CoreEngine(tmp_path / "home", {"fake": {"rules": rules}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                      plugins=pm, cwd=str(tmp_path))


async def _turn(d, eng, sid, text, answer=None):
    start = eng.hub.seq
    await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    asked = []
    while eng._tasks:
        for ev in eng.hub.since(start):
            if isinstance(ev.body, s.ApprovalRequested) and ev.body.request_id in eng._approvals:
                asked.append(ev.body.summary)
                if answer:
                    r = await d.run(s.Command.of(s.ApprovalRespond(request_id=ev.body.request_id, decision=answer,
                                                                   binding=ev.body.binding)))
                    assert r.ok, r.error  # a refused answer would leave the turn waiting forever
        await asyncio.sleep(0.01)
    return asked


def _results(eng):
    return [ev.body.result.text() for ev in eng.hub.since(0) if isinstance(ev.body, s.ToolCompleted)]


def test_agent_changes_machine_and_undoes(env):
    rules = [
        {"match": "bigger", "steps": [{"tool": "machine_set", "args": {"setting": "display.scale", "value": 1.5}},
                                      "done"]},
        {"match": "undo", "steps": [{"tool": "machine_undo", "args": {}}, "done"]},
        {"match": "history", "steps": [{"tool": "machine_changes_list", "args": {}}, "done"]},
    ]
    eng = _engine(env, rules)

    async def main():
        d = Dispatcher(eng, caller="principal:surface")  # the person, as the prompt bar is
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        assert await _turn(d, eng, sid, "bigger") == []      # reversible: runs without asking
        await _turn(d, eng, sid, "undo")
        await _turn(d, eng, sid, "history")
        names = {t["name"] for t in (await d.run(s.Command.of(s.ToolsList()))).result["tools"]}
        assert {"machine_set", "app_open", "system_package_install"} <= names

    asyncio.run(main())
    res = _results(eng)
    first = json.loads(res[0])
    assert first["ok"] and first["say"] == "Screen size: 150%. Undo?"
    assert json.loads(res[1])["say"] == "Undo: Screen size: 150%. Undo?"
    hist = json.loads(res[2])["changes"]
    assert [h["summary"] for h in hist] == ["Undo: Screen size: 150%", "Screen size: 150%", "Set up the machine profile"]
    assert hist[0]["by"].startswith("exo (thread ")


def test_bad_value_is_refused_in_plain_words(env):
    rules = [{"match": "huge", "steps": [{"tool": "machine_set", "args": {"setting": "display.scale", "value": 9}},
                                         "ok"]}]
    eng = _engine(env, rules)

    async def main():
        d = Dispatcher(eng, caller="principal:surface")  # the person, as the prompt bar is
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        await _turn(d, eng, sid, "huge")

    asyncio.run(main())
    assert "Not changed: display.scale must be between 0.5 and 3.0" in _results(eng)[0]


def test_package_install_asks_first(env, monkeypatch):
    calls = []
    monkeypatch.setattr("exo_os.sysd.client.SysdClient.call",
                        lambda self, action, args=None, **kw: calls.append((action, args)) or {"installed": args["names"]})
    rules = [{"match": "install", "steps": [
        {"tool": "system_package_install", "args": {"names": ["htop"], "reason": "to see memory use"}}, "ok"]}]
    eng = _engine(env, rules)

    async def main():
        d = Dispatcher(eng, caller="principal:surface")  # the person, as the prompt bar is
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        return await _turn(d, eng, sid, "install", answer="deny")

    asked = asyncio.run(main())
    assert asked, "installing a package must ask"
    assert calls == []

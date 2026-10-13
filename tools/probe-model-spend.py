#!/usr/bin/env python3
"""Disposable-VM model-spend checks using the installed daemon and journal."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pwd
import sqlite3
import stat
import sys
from pathlib import Path

POLICY = Path("/var/lib/exo/model-spend/policy.json")
JOURNAL = Path("/var/lib/exo/model-spend/exo/journal.sqlite")
SESSION = Path("/tmp/model-spend-acceptance-session")
REPLY = "Offline spend acceptance reply."
LIVE = ("zero_policy", "offline_turn", "mutable_caps_refused", "paid_route_no_http",
        "priced_zero_cap_no_http", "offline_after_refusal")
STOPPED = ("stopped_status", "stopped_generation", "transcripts_readable")


async def daemon_command(command):
    from exo.protocol.client import Client
    async with Client(socket="/var/lib/exo/exo/run/exod.sock", timeout=20) as client:
        return await client.call(command)


async def daemon_turn():
    from exo.protocol import schema as s
    from exo.protocol.client import Client
    async with Client(socket="/var/lib/exo/exo/run/exod.sock", timeout=20) as client:
        created = await client.call(s.SessionCreate(title="offline spend acceptance"))
        sid = created["session"]["id"]
        await client.call(s.TurnSend(session=sid, text="model spend offline check"))
        for _ in range(80):
            current = await client.call(s.SessionGet(session=sid))
            if any(m.get("role") == "assistant" for m in current["messages"]) and REPLY in json.dumps(current["messages"]):
                return sid, current
            await asyncio.sleep(0.25)
        raise AssertionError("offline assistant turn did not complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-acceptance", action="store_true")
    parser.add_argument("--stopped", action="store_true")
    args = parser.parse_args()
    if (not args.vm_acceptance or os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1"
            or sys.platform != "linux" or not Path("/run/systemd/system").is_dir()
            or os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "exo"):
        parser.error("requires explicit disposable Linux VM acceptance as exo")
    checks = []

    def check(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "status": "PASS", "detail": detail})
        except Exception as exc:  # noqa: BLE001 - every failed check is a fixed FAIL result
            checks.append({"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"[:240]})

    try:
        from exo.model_spend import SpendJournal, decode_policy
        from exo.protocol import schema as s
        raw = json.loads(POLICY.read_text())
        assert raw["journal"] == str(JOURNAL)

        if args.stopped:
            def stopped_status():
                current = asyncio.run(daemon_command(s.ModelsList()))
                assert current["spend"]["state"] == "stopped", current["spend"]
                assert SpendJournal(raw).status()["state"] == "stopped"
                return "models.list and the journal report a persistent stop after daemon restart"

            def stopped_generation():
                from exo.core.models.fake import RuleFake
                from exo.core.models.router import Router
                from exo.core.types import Message
                router = Router(roles={"main": ["fake:m"]}, catalog={}, env={},
                                instances={"fake": RuleFake({"reply": "must not answer"})})
                async def collect():
                    return [event async for event in router.stream("main", [Message.user("blocked")], [])]
                events = asyncio.run(collect())
                assert events[-1].kind == "error" and router.spend_status()["state"] == "stopped"
                assert events[-1].attempts == [{"route": "fake:m", "outcome": "spend_stopped"}]
                return "stopped journal refuses even the offline generation path"

            def transcripts_readable():
                from exo.protocol import schema as s
                sid = SESSION.read_text().strip()
                assert sid.startswith("s_")
                sessions = asyncio.run(daemon_command(s.SessionList()))
                assert any(item["id"] == sid for item in sessions["sessions"])
                transcript = asyncio.run(daemon_command(s.SessionGet(session=sid)))
                assert REPLY in json.dumps(transcript["messages"])
                return "the known offline answer remains readable after the journal stop and daemon restart"

            for name, fn in zip(STOPPED, (stopped_status, stopped_generation, transcripts_readable), strict=True):
                check(name, fn)
        else:
            def zero_policy():
                assert os.environ.get("EXO_MODEL_SPEND_REQUIRED") == "1"
                assert os.environ.get("EXO_MODEL_SPEND_POLICY") == str(POLICY)
                assert raw["principal"] == "exo" and raw["daily_usd"] == raw["monthly_usd"] == "0"
                assert raw["routes"] == {}
                for path, owner, mode in ((POLICY.parent, 0, 0o755), (POLICY, 0, 0o644),
                                          (JOURNAL.parent, os.getuid(), 0o700), (JOURNAL, os.getuid(), 0o600)):
                    info = path.lstat()
                    assert info.st_uid == owner and stat.S_IMODE(info.st_mode) == mode, path
                with sqlite3.connect(JOURNAL.as_uri() + "?mode=ro", uri=True) as db:
                    assert db.execute("SELECT id,principal FROM identity").fetchall() == [(raw["journal_id"], "exo")]
                    assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
                return "root zero-cap policy and exo journal have fixed ownership, paths and matching UUID"

            def offline_turn():
                status = asyncio.run(daemon_command(s.ModelsList()))["spend"]
                assert status["state"] == "offline" and status["daily_cap_usd"] == "0.000000000"
                sid, transcript = asyncio.run(daemon_turn())
                assert REPLY in json.dumps(transcript["messages"])
                SESSION.write_text(sid)
                with sqlite3.connect(JOURNAL.as_uri() + "?mode=ro", uri=True) as db:
                    assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
                return "installed exod completed an offline fake turn without a reservation"

            def mutable_caps_refused():
                from exo.core.models.fake import RuleFake
                from exo.core.models.router import Router
                altered = {**raw, "daily_usd": "1000000", "monthly_usd": "1000000",
                           "routes": {"fake:m": {"input": "0"}}}
                router = Router.from_config({"spend": altered, "roles": {"main": ["fake:m"]}},
                                            catalog={}, env={}, instances={"fake": RuleFake({"reply": "offline"})})
                assert router.spend.daily == router.spend.monthly == 0 and router.spend.prices == {}
                return "mutable config could not raise either cap or install a paid route"

            def paid_route_no_http():
                import httpx
                from exo.core.models.router import Router
                from exo.core.types import Message
                seen = []
                client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: seen.append(req)))
                router = Router(roles={"main": ["paid:m"]}, providers={"paid": {
                    "transport": "openai", "base_url": "https://api.openai.com/v1"}},
                    catalog={}, env={}, client=client)
                from exo.core.models.openai_chat import OpenAIChatProvider
                assert type(router.credentials("paid")[0].provider) is OpenAIChatProvider
                async def collect():
                    try:
                        return [event async for event in router.stream("main", [Message.user("blocked")], [])]
                    finally:
                        await client.aclose()
                events = asyncio.run(collect())
                assert events[-1].kind == "error" and not events[-1].retryable and not seen
                assert events[-1].attempts == [{"route": "paid:m", "outcome": "spend_refused"}]
                assert (events[-1].error or "").startswith("Model request refused:"), events[-1].error
                assert "no pinned" in (events[-1].error or "").lower(), events[-1].error
                assert SpendJournal.inspect(raw)["state"] == "offline"
                with sqlite3.connect(JOURNAL.as_uri() + "?mode=ro", uri=True) as db:
                    assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
                return "instantiated OpenAI transport was refused before mock HTTP; journal stayed usable"

            def priced_zero_cap_no_http():
                import httpx
                from exo.core.models.openai_chat import OpenAIChatProvider
                from exo.core.models.router import Router
                from exo.core.types import Message
                policy_path = Path(os.environ["EXO_OS_VM_PRICED_POLICY"])
                assert policy_path.parent.parent == Path("/run")
                priced = decode_policy(policy_path.read_bytes())
                ref = "native:gpt-4.1-2025-04-14"
                assert priced["principal"] == raw["principal"] == "exo"
                assert priced["journal"] == raw["journal"] and priced["journal_id"] == raw["journal_id"]
                assert priced["daily_usd"] == priced["monthly_usd"] == "0"
                assert set(priced["routes"]) == {ref}
                assert SpendJournal.inspect(priced)["state"] == "ready"
                seen = []
                client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: seen.append(request)))
                original = os.environ["EXO_MODEL_SPEND_POLICY"]
                try:
                    os.environ["EXO_MODEL_SPEND_POLICY"] = str(policy_path)
                    router = Router(roles={"main": [ref]}, providers={"native": {
                        "transport": "openai", "base_url": "https://api.openai.com/v1"}},
                        catalog={}, env={}, client=client)
                    assert type(router.credentials("native")[0].provider) is OpenAIChatProvider
                    assert router.spend.prices[ref].maximum(100) > 0
                    async def collect():
                        return [event async for event in router.stream("main", [Message.user("blocked")], [])]
                    events = asyncio.run(collect())
                    assert events[-1].kind == "error" and not events[-1].retryable and not seen
                    assert events[-1].attempts == [{"route": ref, "outcome": "spend_refused"}]
                    assert (events[-1].error or "").startswith("Model request refused:")
                    assert "day model-spend cap" in (events[-1].error or "")
                    assert router.spend_status()["state"] == "ready"
                finally:
                    os.environ["EXO_MODEL_SPEND_POLICY"] = original
                    asyncio.run(client.aclose())
                assert SpendJournal.inspect(raw)["state"] == "offline"
                with sqlite3.connect(JOURNAL.as_uri() + "?mode=ro", uri=True) as db:
                    assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
                return "root-owned temporary priced native route hit zero cap before mock HTTP; persistent policy stayed offline"

            def offline_after_refusal():
                _, transcript = asyncio.run(daemon_turn())
                assert REPLY in json.dumps(transcript["messages"])
                assert SpendJournal.inspect(raw)["state"] == "offline"
                return "a new daemon offline turn succeeds after the known unpriced refusal"

            for name, fn in zip(LIVE, (zero_policy, offline_turn, mutable_caps_refused, paid_route_no_http,
                                       priced_zero_cap_no_http, offline_after_refusal), strict=True):
                check(name, fn)
    except Exception as exc:  # noqa: BLE001 - setup failure emits the full fixed result set
        names = STOPPED if args.stopped else LIVE
        for name in names[len(checks):]:
            checks.append({"name": name, "status": "FAIL", "detail": f"setup refused: {type(exc).__name__}: {exc}"[:240]})
    expected = STOPPED if args.stopped else LIVE
    if tuple(x["name"] for x in checks) != expected:
        checks = [{"name": name, "status": "FAIL", "detail": "invalid probe result shape"} for name in expected]
    print(json.dumps({"checks": checks}, separators=(",", ":")))
    return 0 if all(x["status"] == "PASS" for x in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

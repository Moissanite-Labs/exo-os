"""What did Exo do today? A plain answer built from the principal's ledger (DESIGN.md §9.3, item 4).

The ledger is the record, so the timeline is only a reading of it: machine changes (and the ones put
back), system actions with their signed receipts, the times Exo asked and what the person answered, and
what was blocked. Times are local and in 12-hour form.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable


def _local(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


def clock(t: dt.datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def resolve_day(day: str | None, now: dt.datetime | None = None) -> dt.date:
    now = now or dt.datetime.now().astimezone()
    if not day or day == "today":
        return now.date()
    if day == "yesterday":
        return now.date() - dt.timedelta(days=1)
    return dt.date.fromisoformat(day)


def items(events: Iterable[dict[str, Any]], day: dt.date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    asked: dict[str, dict[str, Any]] = {}
    for ev in events:
        t = _local(ev["ts"])
        if t.date() != day:
            continue
        b = ev.get("body") or {}
        item = _item(ev, b)
        if item is None:
            continue
        item["time"] = clock(t)
        if item.pop("_ask", False):
            asked[ev["id"]] = item
        if item.pop("_answer", False):
            ref = next((r for r in ev.get("refs") or [] if r in asked), None)
            if ref:  # fold the answer into the line that asked
                asked[ref]["what"] += f" You said {'yes' if b.get('approved') else 'no'}."
                continue
        out.append(item)
    return out


def _item(ev: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    if b.get("os") == "exo-os":
        phase = b.get("phase")
        if phase == "change.applied":
            if b.get("by") == "setup":
                return None
            return {"what": b.get("summary", "A change"), "by": _who(b.get("by")), "undo": b.get("change")}
        if phase == "change.reverted":
            return {"what": f"Tried: {b.get('summary', 'a change')}. It did not work, so it was put back.",
                    "by": _who(b.get("by"))}
        if phase == "system.action":
            r = b.get("receipt") or {}
            res, args = r.get("result") or {}, r.get("args") or {}
            if r.get("action") == "package.install":
                names = ", ".join(args.get("names") or [])
                if r.get("ok"):
                    row = {"what": f"Installed {names}.", "by": "Exo"}
                    if res.get("pre") is not None and res.get("post") is not None:
                        row["undo"] = f"system {res['pre']} {res['post']}"
                    return row
                return {"what": f"Tried to install {names}; it did not work.", "by": "Exo"}
            if r.get("action") == "snapshot.undo" and r.get("ok"):
                return {"what": "Put the system back as it was before an install.", "by": "Exo"}
        return None
    if b.get("gate"):
        phase, summary = b.get("phase"), ((b.get("classification") or {}).get("summary") or b.get("tool", "an action"))
        if phase == "decision" and b.get("outcome") == "ask":
            return {"what": f"Asked you before: {summary}.", "by": "Exo", "_ask": True}
        if phase == "decision" and b.get("outcome") == "deny":
            return {"what": f"Blocked: {summary}.", "by": "Exo's rules"}
        if phase == "approval":
            return {"what": f"You said {'yes' if b.get('approved') else 'no'}.", "by": "you", "_answer": True}
        if phase == "grant":
            return {"what": "You said Exo may do this again without asking, for a while.", "by": "you"}
    return None


def _who(by: str | None) -> str:
    if not by or by in ("person", "shahid"):
        return "you"
    if by.startswith("exo"):
        return "Exo"
    return by


def read(store: Path, day: dt.date) -> list[dict[str, Any]]:
    from exo.ledger import Ledger

    path = store / "ledger.sqlite"
    if not path.exists():
        return []
    start = dt.datetime.combine(day, dt.time.min).astimezone() - dt.timedelta(hours=14)
    return items(Ledger(path).iter(since=start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")), day)


def say(day_items: list[dict[str, Any]], day: dt.date, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    when = "today" if day == today else ("yesterday" if day == today - dt.timedelta(days=1) else day.strftime("%B %d").replace(" 0", " "))
    if not day_items:
        return f"Exo did nothing {when}." if when in ("today", "yesterday") else f"Exo did nothing on {when}."
    lines = [f"{i['time']}  {i['what']}" + (f" ({i['by']})" if i.get("by") not in (None, "Exo") else "")
             for i in day_items]
    head = f"What happened {when}:" if when in ("today", "yesterday") else f"What happened on {when}:"
    return "\n".join([head, *lines])

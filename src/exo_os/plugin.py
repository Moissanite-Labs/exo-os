"""The exo-os plugin for Exo: tools that let the agent look at and change this machine.

Loaded by Exo through the `exo.plugins` entry point. The tools carry Exo's registry risk labels
("low": the agent runs it and it is logged; "high": the gate asks the person first). The names are
chosen so the gate rules Exo OS writes at setup (setup.py: `system_package_*` and
`system_snapshot_undo` ask, `machine_*` acts, `app_open` acts) sort them the way DESIGN.md §5.2 wants
without any change to Exo:

    machine_show, machine_changes_list, timeline_show, system_snapshots_list   low    read: runs, logged
    machine_set, machine_app_put                                                low    reversible: runs, logged, shows "Undo?"
    machine_undo, machine_app_unbind, app_open                                  low    runs, logged
    system_package_install, system_snapshot_undo                                high   asks, in plain words

Every change still goes through the change engine, so it is checked, put back if a check fails,
committed to the machine repo, and written to the principal's ledger.
"""
from __future__ import annotations

import json
from typing import Any

from . import apps, paths
from .changes import ChangeEngine
from .ledger import open_sink
from .profile import FIELDS, ProfileError
from .words import LABELS, set_summary, value_words

PLUGIN_MANIFEST = {"name": "exo-os", "version": "0.1.0",
                   "description": "Exo OS: machine profile, apps and system changes, gated and undoable"}


class ToolFail(Exception):
    pass


def _engine() -> ChangeEngine:
    return ChangeEngine(paths.machine_repo(), paths.config_home(), ledger=open_sink(paths.store(), paths.exo_home()))


def _by(kw: dict[str, Any]) -> str:
    return f"exo (thread {kw['session_id']})" if kw.get("session_id") else "exo"


def _error(msg: str) -> Exception:
    try:
        from exo.core.tools.registry import ToolError
        return ToolError(msg)
    except ImportError:  # pragma: no cover - outside Exo
        return ToolFail(msg)


def _result(r: Any) -> str:
    out = {"ok": r.ok, "say": r.message, "change": r.change}
    if not r.ok and r.checks:
        out["why"] = r.checks[-1]
    return json.dumps(out)


def machine_show(args: dict, **kw: Any) -> str:
    prof = _engine().profile()
    settings = {path: {"label": LABELS[path], "value": prof.get(path), "words": value_words(path, prof.get(path))}
                for path in FIELDS}
    return json.dumps({"settings": settings, "apps": prof.data["apps"]})


def machine_set(args: dict, **kw: Any) -> str:
    path, value = args["setting"], args["value"]
    eng = _engine()
    try:
        new = eng.profile().set_value(path, value)
    except ProfileError as e:
        raise _error(f"Not changed: {e}") from None
    return _result(eng.apply(new, set_summary(path, value), by=_by(kw)))


def machine_app_put(args: dict, **kw: Any) -> str:
    app = {k: args[k] for k in ("name", "url", "command", "key", "workspace", "autostart") if args.get(k) is not None}
    eng = _engine()
    prof = eng.profile()
    verb = "Changed" if prof.app(app.get("name", "")) else "Added"
    try:
        new = prof.put_app(app)
    except ProfileError as e:
        raise _error(f"Not changed: {e}") from None
    return _result(eng.apply(new, f"{verb} {app['name']}" + (f" on {app['key']}" if app.get("key") else ""),
                             by=_by(kw)))


def machine_app_unbind(args: dict, **kw: Any) -> str:
    eng = _engine()
    try:
        new = eng.profile().drop_app(args["name"])
    except ProfileError as e:
        raise _error(f"Not changed: {e}") from None
    return _result(eng.apply(new, f"Removed {args['name']}", by=_by(kw)))


def machine_changes_list(args: dict, **kw: Any) -> str:
    return json.dumps({"changes": _engine().history(int(args.get("limit") or 10))})


def machine_undo(args: dict, **kw: Any) -> str:
    return _result(_engine().undo(args.get("change") or None, by=_by(kw)))


def app_open(args: dict, **kw: Any) -> str:
    try:
        return apps.open_app(_engine().profile(), args["name"])
    except KeyError:
        names = ", ".join(a["name"] for a in _engine().profile().data["apps"])
        raise _error(f"There is no app called {args['name']}. Apps: {names}") from None


def system_package_install(args: dict, **kw: Any) -> str:
    from .sysd.client import SysdClient, SysdError

    try:
        out = SysdClient().call("package.install", {"names": args["names"], "reason": args.get("reason", "")})
    except SysdError as e:
        raise _error(str(e)) from None
    return json.dumps(out)


def system_snapshot_undo(args: dict, **kw: Any) -> str:
    from .sysd.client import SysdClient, SysdError

    try:
        return json.dumps(SysdClient().call("snapshot.undo", {"pre": args["pre"], "post": args["post"]}))
    except SysdError as e:
        raise _error(str(e)) from None


def system_snapshots_list(args: dict, **kw: Any) -> str:
    from .sysd.client import SysdClient, SysdError

    try:
        return json.dumps(SysdClient().call("snapshot.list", {"limit": int(args.get("limit") or 10)}))
    except SysdError as e:
        raise _error(str(e)) from None


def timeline_show(args: dict, **kw: Any) -> str:
    from . import timeline

    store = paths.store()
    if store is None:
        raise _error("There is no Exo store on this machine yet.")
    try:
        day = timeline.resolve_day(args.get("day"))
    except ValueError:
        raise _error("day must be today, yesterday or a date like 2026-10-12") from None
    rows = timeline.read(store, day)
    return json.dumps({"say": timeline.say(rows, day), "items": rows,
                       "panel_hint": "a tasks panel with one line per item reads well"})


def _schema(desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"description": desc, "parameters": {"type": "object", "properties": props, "required": required,
                                                "additionalProperties": False}}


SETTING = {"type": "string", "enum": sorted(FIELDS),
           "description": "; ".join(f"{k} = {LABELS[k]}" for k in sorted(FIELDS))}
APP_PROPS = {
    "name": {"type": "string", "description": "Short name, such as WhatsApp"},
    "url": {"type": "string", "description": "For a web app: its https address"},
    "command": {"type": "string", "description": "For an installed program: its command"},
    "key": {"type": "string", "description": "Key combination such as SUPER+1"},
    "workspace": {"type": "integer", "minimum": 1, "maximum": 10},
    "autostart": {"type": "boolean", "description": "Open at login"},
}

TOOLS = [
    ("machine_show", machine_show, "low", _schema(
        "Show this machine's settings (screen and text size, keyboard languages, idle and lock times, theme) "
        "and the apps on keys.", {}, [])),
    ("machine_set", machine_set, "low", _schema(
        "Change one machine setting. The change is checked, logged and can be undone; if a check fails it is "
        "put back automatically. Tell the person the 'say' line.",
        {"setting": SETTING, "value": {"description": "New value: number, true/false, text, or a list of "
                                                      "keyboard layout codes (us, ara, pk, ...)"}},
        ["setting", "value"])),
    ("machine_app_put", machine_app_put, "low", _schema(
        "Add an app to the machine (on a key and a workspace), or change one with the same name.",
        APP_PROPS, ["name"])),
    ("machine_app_unbind", machine_app_unbind, "low", _schema(
        "Take an app off the machine profile (its key and window rule). Can be undone.",
        {"name": APP_PROPS["name"]}, ["name"])),
    ("machine_changes_list", machine_changes_list, "low", _schema(
        "List recent changes to this machine, newest first, with who made each.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, [])),
    ("machine_undo", machine_undo, "low", _schema(
        "Undo the latest machine change, or the one named by its change id. The undo is itself a change "
        "that can be undone.", {"change": {"type": "string"}}, [])),
    ("app_open", app_open, "low", _schema(
        "Open one of the person's apps, or bring it to the front if it is already open.",
        {"name": APP_PROPS["name"]}, ["name"])),
    ("system_package_install", system_package_install, "high", _schema(
        "Install system packages. A system snapshot is taken first; full undo may need a restart. Always asks "
        "the person first.",
        {"names": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
         "reason": {"type": "string", "description": "One plain sentence: why"}}, ["names", "reason"])),
    ("system_snapshot_undo", system_snapshot_undo, "high", _schema(
        "Undo one package install: put the system's files back as they were at its 'before' snapshot. Home "
        "and the Exo store are not touched. Always asks the person first.",
        {"pre": {"type": "integer"}, "post": {"type": "integer"}}, ["pre", "post"])),
    ("timeline_show", timeline_show, "low", _schema(
        "What Exo did on a day, in plain words, from the person's signed record: changes to this computer and "
        "the ones put back, installs, every time Exo asked and what the person answered, and what was blocked. "
        "Use it for 'what did you do today?'.",
        {"day": {"type": "string", "description": "today, yesterday, or YYYY-MM-DD"}}, [])),
    ("system_snapshots_list", system_snapshots_list, "low", _schema(
        "List system snapshots (taken before package installs and system changes).",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, [])),
]


def register(ctx: Any) -> None:
    for name, handler, risk, schema in TOOLS:
        ctx.register_tool(name, "exo-os", {"name": name, **schema}, handler=handler,
                          description=schema["description"], risk=risk)
    ctx.register_command("machine", _command, "Machine profile: show, history, undo", "[show|history|undo [id]]")


def _command(raw: str) -> str:
    from .cli import main
    import contextlib
    import io

    words = raw.split() or ["show"]
    if words[0] not in ("show", "history", "undo"):
        return "Use: /machine show | history | undo [change]"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        main(words + (["--by", "person"] if words[0] == "undo" else []))
    return buf.getvalue().strip()

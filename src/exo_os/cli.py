"""exo-os: look at and change this machine through the change engine.

Every command that changes something goes through exo_os.changes, so it is checked, logged in the
principal's ledger, committed to the machine repo, and can be undone with `exo-os undo`.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import apps, paths
from .changes import ChangeEngine, Result
from .ledger import open_sink
from .profile import FIELDS, Profile, ProfileError
from .words import set_summary


def engine() -> ChangeEngine:
    return ChangeEngine(paths.machine_repo(), paths.config_home(), ledger=open_sink(paths.store(), paths.exo_home()))


def parse_value(path: str, text: str) -> Any:
    typ = FIELDS[path][0] if path in FIELDS else str
    if typ is list:
        return [x.strip() for x in text.split(",") if x.strip()]
    if typ is bool:
        if text.lower() in ("on", "true", "yes", "1"):
            return True
        if text.lower() in ("off", "false", "no", "0"):
            return False
        raise ProfileError(f"{path} must be on or off")
    if typ is int:
        try:
            return int(text)
        except ValueError:
            raise ProfileError(f"{path} must be a whole number") from None
    if typ == (int, float):
        try:
            return float(text.rstrip("%")) / (100 if text.endswith("%") else 1)
        except ValueError:
            raise ProfileError(f"{path} must be a number") from None
    return text


def _print_result(r: Result, as_json: bool) -> int:
    if as_json:
        print(json.dumps(r.as_dict(), indent=2))
    else:
        print(r.message)
        if not r.ok and r.checks:
            print("  " + r.checks[-1], file=sys.stderr)
    return 0 if r.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="exo-os", description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the machine repo and apply the profile").add_argument("--from", dest="src")
    sub.add_parser("show", help="print the profile, or one setting").add_argument("path", nargs="?")
    p = sub.add_parser("set", help="change one setting")
    p.add_argument("path", choices=sorted(FIELDS))
    p.add_argument("value")
    p.add_argument("--by", default="person")
    sub.add_parser("history", help="what changed, newest first").add_argument("-n", type=int, default=20)
    p = sub.add_parser("undo", help="put back the latest change, or the one named")
    p.add_argument("change", nargs="?")
    p.add_argument("--by", default="person")
    sub.add_parser("redeploy", help="write the desktop config again from the profile")
    sub.add_parser("today", help="what Exo did today (or --day yesterday / YYYY-MM-DD)").add_argument(
        "--day", default="today")
    a = sub.add_parser("app", help="apps on keys").add_subparsers(dest="app_cmd", required=True)
    p = a.add_parser("open")
    p.add_argument("name")
    p.add_argument("--background", action="store_true")
    a.add_parser("list")
    p = a.add_parser("add")
    p.add_argument("name")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url")
    g.add_argument("--command")
    p.add_argument("--key")
    p.add_argument("--workspace", type=int)
    p.add_argument("--class", dest="cls")
    p.add_argument("--autostart", action="store_true")
    p.add_argument("--by", default="person")
    p = a.add_parser("remove")
    p.add_argument("name")
    p.add_argument("--by", default="person")
    sy = sub.add_parser("system", help="system snapshots and packages, through exo-sysd").add_subparsers(
        dest="sys_cmd", required=True)
    sy.add_parser("status")
    sy.add_parser("snapshots").add_argument("-n", type=int, default=10)
    p = sy.add_parser("install")
    p.add_argument("names", nargs="+")
    p.add_argument("--reason", default="asked from the command line")
    p = sy.add_parser("undo", help="put the system back as it was before one install")
    p.add_argument("pre", type=int)
    p.add_argument("post", type=int)
    args = ap.parse_args(argv)

    try:
        return _run(args)
    except ProfileError as e:
        print(f"Not changed: {e}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    eng = engine()
    if args.cmd == "init":
        prof = Profile.loads(open(args.src).read()) if args.src else None
        return _print_result(eng.init(prof), args.json)
    if args.cmd == "show":
        prof = eng.profile()
        if args.path:
            value = prof.get(args.path) if "." in args.path else prof.data[args.path]
            print(json.dumps(value) if args.json or not isinstance(value, str) else value)
        else:
            print(json.dumps(prof.data, indent=2) if args.json else prof.dumps())
        return 0
    if args.cmd == "set":
        value = parse_value(args.path, args.value)
        return _print_result(eng.apply(eng.profile().set_value(args.path, value), set_summary(args.path, value),
                                       by=args.by), args.json)
    if args.cmd == "history":
        items = eng.history(args.n)
        if args.json:
            print(json.dumps(items, indent=2))
        for h in items if not args.json else []:
            print(f"{h['change']}  {h['time'][:16].replace('T', ' ')}  {h['summary']}" + (f"  ({h['by']})" if h["by"] else ""))
        return 0
    if args.cmd == "undo":
        return _print_result(eng.undo(args.change, by=args.by), args.json)
    if args.cmd == "today":
        from . import timeline

        store = paths.store()
        day = timeline.resolve_day(args.day)
        rows = timeline.read(store, day) if store else []
        print(json.dumps(rows, indent=2) if args.json else timeline.say(rows, day))
        return 0
    if args.cmd == "redeploy":
        return _print_result(eng.redeploy(), args.json)
    if args.cmd == "system":
        from .sysd.client import SysdClient, SysdError

        try:
            if args.sys_cmd == "status":
                out = SysdClient().call("status")
            elif args.sys_cmd == "snapshots":
                out = SysdClient().call("snapshot.list", {"limit": args.n})
            elif args.sys_cmd == "undo":
                out = SysdClient().call("snapshot.undo", {"pre": args.pre, "post": args.post})
            else:
                out = SysdClient().call("package.install", {"names": args.names, "reason": args.reason})
        except SysdError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json or args.sys_cmd == "status":
            print(json.dumps(out, indent=2))
        elif args.sys_cmd == "snapshots":
            for snap in out["snapshots"]:
                print(f"{snap['number']:>5}  {snap['date'][:16]}  {snap['description']}")
        elif args.sys_cmd == "undo":
            print(f"Put the system back as it was at snapshot {out['undone'][0]}.")
        else:
            print(f"Installed {', '.join(out['installed'])}. Snapshots before and after: {out['pre']}, {out['post']}."
                  f" To undo: exo-os system undo {out['pre']} {out['post']}")
        return 0
    if args.cmd == "app":
        prof = eng.profile()
        if args.app_cmd == "open":
            try:
                print(apps.open_app(prof, args.name, background=args.background))
            except KeyError:
                print(f"There is no app called {args.name}.", file=sys.stderr)
                return 1
            return 0
        if args.app_cmd == "list":
            items = prof.data["apps"]
            if args.json:
                print(json.dumps(items, indent=2))
            for app in items if not args.json else []:
                print(f"{app['name']:<16} {app.get('key') or '':<14} {app.get('url') or app.get('command')}")
            return 0
        if args.app_cmd == "add":
            app = {"name": args.name, "url": args.url, "command": args.command, "key": args.key,
                   "workspace": args.workspace, "class": args.cls, "autostart": args.autostart or None}
            app = {k: v for k, v in app.items() if v is not None}
            verb = "Changed" if prof.app(args.name) else "Added"
            return _print_result(eng.apply(prof.put_app(app), f"{verb} {args.name}"
                                           + (f" on {args.key}" if args.key else ""), by=args.by), args.json)
        if args.app_cmd == "remove":
            return _print_result(eng.apply(prof.drop_app(args.name), f"Removed {args.name}", by=args.by), args.json)
    return 2


if __name__ == "__main__":
    sys.exit(main())

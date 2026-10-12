"""The complete list of things exo-sysd will do as root. Nothing else is reachable.

Each action takes checked arguments and runs fixed argv lists (never a shell string). Bootloader,
partitions, firmware, kernel options and reboots are deliberately absent: those stay a person's job
(DESIGN.md §5.2). Package installs are snapshotted by snap-pac's pacman hook; when snap-pac is
missing, exo-sysd takes the pre and post snapshots itself.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

PKG_RE = re.compile(r"^[a-z0-9@_+][a-z0-9@._+-]{0,99}$")
DESC_RE = re.compile(r"^[\w .,:;'()/+-]{1,120}$")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class ActionError(Exception):
    """Refused or failed, with a reason a person can read."""


def run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=1800,
                          env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})


@dataclass
class Actions:
    runner: Runner = run
    snapper_config: str = "root"

    def has(self, binary: str) -> bool:
        return shutil.which(binary, path="/usr/bin:/bin") is not None

    def _ok(self, argv: list[str]) -> str:
        r = self.runner(argv)
        if r.returncode != 0:
            raise ActionError(f"{argv[0]} failed: {(r.stderr or r.stdout).strip()[-400:]}")
        return r.stdout

    # -- actions

    def status(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"snapper": self.has("snapper"), "snap_pac": self._snap_pac(), "pacman": self.has("pacman")}

    def snapshot_create(self, args: dict[str, Any]) -> dict[str, Any]:
        desc = str(args.get("description", "exo-os"))
        if not DESC_RE.match(desc):
            raise ActionError("description has characters that are not allowed")
        out = self._ok(["snapper", "-c", self.snapper_config, "create", "--type", "single", "--cleanup-algorithm",
                        "number", "--print-number", "--description", desc, "--userdata", "exo-os=yes"])
        return {"snapshot": int(out.strip().splitlines()[-1])}

    def snapshot_list(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _int(args.get("limit", 10), 1, 100)
        out = self._ok(["snapper", "--jsonout", "-c", self.snapper_config, "list", "--disable-used-space"])
        rows = json.loads(out).get(self.snapper_config, [])
        rows = [{"number": r["number"], "date": r.get("date", ""), "description": r.get("description", ""),
                 "type": r.get("type", "")} for r in rows if r.get("number")]
        return {"snapshots": rows[-limit:][::-1]}

    def package_install(self, args: dict[str, Any]) -> dict[str, Any]:
        names = args.get("names")
        if not isinstance(names, list) or not 1 <= len(names) <= 10:
            raise ActionError("give 1 to 10 package names")
        for n in names:
            if not isinstance(n, str) or not PKG_RE.match(n):
                raise ActionError(f"{n!r} is not a package name")
        missing = [n for n in names if self.runner(["pacman", "-Si", "--", n]).returncode != 0]
        if missing:
            raise ActionError(f"not in the package repositories: {', '.join(missing)}")
        own = not self._snap_pac()
        pre = self.snapshot_create({"description": f"before installing {' '.join(names)}"[:120]}) if own else None
        self._ok(["pacman", "-S", "--needed", "--noconfirm", "--", *names])
        post = self.snapshot_create({"description": f"after installing {' '.join(names)}"[:120]}) if own else None
        latest = self.snapshot_list({"limit": 2})["snapshots"] if not own else []
        return {"installed": names, "pre": pre["snapshot"] if pre else _num(latest, 1),
                "post": post["snapshot"] if post else _num(latest, 0)}

    def _snap_pac(self) -> bool:
        return self.runner(["pacman", "-Q", "snap-pac"]).returncode == 0

    def table(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {"status": self.status, "snapshot.create": self.snapshot_create, "snapshot.list": self.snapshot_list,
                "package.install": self.package_install}


def _int(v: Any, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ActionError(f"expected a whole number from {lo} to {hi}")
    return v


def _num(rows: list[dict[str, Any]], i: int) -> int | None:
    return rows[i]["number"] if len(rows) > i else None

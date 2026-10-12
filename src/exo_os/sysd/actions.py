"""The complete list of things exo-sysd will do as root. Nothing else is reachable.

Each action takes checked arguments and runs fixed argv lists (never a shell string). Bootloader,
partitions, firmware, kernel options and reboots are deliberately absent: those stay a person's job
(DESIGN.md §5.2). Package installs are snapshotted by snap-pac's pacman hook; when snap-pac is
missing, exo-sysd takes the pre and post snapshots itself.

Arch does not support partial upgrades (refreshing the package list, then installing one package
against it), so an install refreshes the list and then installs with a full upgrade (-Su).
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
    keyring: str = "/etc/pacman.d/gnupg"

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
        return {"snapshot": self._snap("single", str(args.get("description", "exo-os")))}

    def _snap(self, kind: str, desc: str, pre: int | None = None) -> int:
        if not DESC_RE.match(desc):
            raise ActionError("description has characters that are not allowed")
        argv = ["snapper", "-c", self.snapper_config, "create", "--type", kind, "--cleanup-algorithm", "number",
                "--print-number", "--description", desc, "--userdata", "exo-os=yes"]
        if pre is not None:
            argv += ["--pre-number", str(pre)]
        return int(self._ok(argv).strip().splitlines()[-1])

    def snapshot_list(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _int(args.get("limit", 10), 1, 100)
        out = self._ok(["snapper", "--jsonout", "-c", self.snapper_config, "list", "--disable-used-space"])
        rows = json.loads(out).get(self.snapper_config, [])
        rows = [{"number": r["number"], "date": r.get("date", ""), "description": r.get("description", ""),
                 "type": r.get("type", ""), "pre": r.get("pre-number")} for r in rows if r.get("number")]
        return {"snapshots": rows[-limit:][::-1]}

    def package_install(self, args: dict[str, Any]) -> dict[str, Any]:
        names = args.get("names")
        if not isinstance(names, list) or not 1 <= len(names) <= 10:
            raise ActionError("give 1 to 10 package names")
        for n in names:
            if not isinstance(n, str) or not PKG_RE.match(n):
                raise ActionError(f"{n!r} is not a package name")
        if not self.keyring_ready():
            raise ActionError("the package keyring is still being set up after first start; try again in a minute")
        self._ok(["pacman", "-Sy", "--noconfirm"])
        missing = [n for n in names if self.runner(["pacman", "-Si", "--", n]).returncode != 0]
        if missing:
            raise ActionError(f"not in the package repositories: {', '.join(missing)}")
        own = not self._snap_pac()
        desc = f"install {' '.join(names)}"[:120]
        pre = self._snap("pre", desc) if own else None
        self._ok(["pacman", "-Su", "--needed", "--noconfirm", "--", *names])
        post = self._snap("post", desc, pre) if own else None
        if not own:  # snap-pac's pair: the newest post snapshot and the pre it points to
            newest = next((r for r in self.snapshot_list({"limit": 20})["snapshots"] if r["type"] == "post"), None)
            pre, post = (newest["pre"], newest["number"]) if newest else (None, None)
        return {"installed": names, "pre": pre, "post": post}

    def snapshot_undo(self, args: dict[str, Any]) -> dict[str, Any]:
        """Put the system's files back as they were before one install: snapper undochange PRE..POST.

        Only a pre/post pair is accepted (a post snapshot whose pre is the given one), so this undoes one
        recorded change and nothing wider. Files in home and the Exo store are not on this subvolume and
        are never touched."""
        pre, post = _int(args.get("pre"), 1, 10**6), _int(args.get("post"), 1, 10**6)
        rows = {r["number"]: r for r in self.snapshot_list({"limit": 100})["snapshots"]}
        if pre not in rows or post not in rows:
            raise ActionError(f"no snapshots {pre} and {post}")
        if rows[post].get("pre") != pre:
            raise ActionError(f"snapshot {post} is not the 'after' of {pre}; only one change can be undone at a time")
        self._ok(["snapper", "-c", self.snapper_config, "undochange", f"{pre}..{post}"])
        return {"undone": [pre, post]}

    def keyring_ready(self) -> bool:
        """exo-os-pacman-init.service makes this machine's keyring at first boot."""
        from pathlib import Path

        return Path(self.keyring).joinpath("trustdb.gpg").exists()

    def _snap_pac(self) -> bool:
        return self.runner(["pacman", "-Q", "snap-pac"]).returncode == 0

    def table(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {"status": self.status, "snapshot.create": self.snapshot_create, "snapshot.list": self.snapshot_list,
                "package.install": self.package_install, "snapshot.undo": self.snapshot_undo}


def _int(v: Any, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ActionError(f"expected a whole number from {lo} to {hi}")
    return v

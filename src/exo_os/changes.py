"""The change engine: every change to the machine profile is checked, logged and reversible.

The life of one change (DESIGN.md §5.3, for the parts that run as the person):

    propose   the new profile is validated and rendered; ledger: change.proposed
    apply     machine.toml and the rendered files are written to the machine repo and deployed
    check     each check runs (Hyprland --verify-config, live reload, ...)
    pass      git commit; ledger: change.applied with the diff; the person sees one line and "Undo?"
    fail      everything is put back as it was; ledger: change.reverted; "That did not work, so I put it back."

The machine repo is a git repo (default <store>/machine). It holds machine.toml and a copy of every
rendered file under rendered/, so `git log` is the history of the machine and each commit is a
readable diff. Undo is a new change that restores the profile from before the one being undone, so
an undo is itself logged and can be undone.
"""
from __future__ import annotations

import difflib
import fcntl
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .checks import DEFAULT_CHECKS, Check
from .ledger import NullSink, Sink
from .paths import DESKTOP_MARK, owns_desktop
from .profile import Profile, ProfileError
from .render.hyprland import render as render_hyprland

INIT_SUMMARY = "Set up the machine profile"
NOT_OURS = "This desktop is not run by Exo OS, so I left it alone."
Renderer = Callable[[Profile], dict[str, str]]
GIT_ENV = {"GIT_AUTHOR_NAME": "Exo OS", "GIT_AUTHOR_EMAIL": "exo-os@localhost",
           "GIT_COMMITTER_NAME": "Exo OS", "GIT_COMMITTER_EMAIL": "exo-os@localhost"}


@dataclass
class Result:
    ok: bool
    message: str
    change: str | None = None          # commit id of the applied change
    diff: str = ""
    checks: list[str] = field(default_factory=list)
    reverted: bool = False

    def as_dict(self) -> dict:
        return {"ok": self.ok, "message": self.message, "change": self.change, "diff": self.diff,
                "checks": self.checks, "reverted": self.reverted}


class ChangeEngine:
    def __init__(self, repo: Path, config_home: Path, *, ledger: Sink | None = None,
                 checks: list[Check] | None = None, renderer: Renderer = render_hyprland,
                 owns: Callable[[Path], bool] = owns_desktop) -> None:
        self.repo = Path(repo)
        self.config_home = Path(config_home)
        self.ledger = ledger or NullSink()
        self.checks = DEFAULT_CHECKS if checks is None else checks
        self.renderer = renderer
        self.owns = owns

    # -- reading

    def profile(self) -> Profile:
        path = self.repo / "machine.toml"
        return Profile.loads(path.read_text()) if path.exists() else Profile.default()

    def history(self, limit: int = 20) -> list[dict]:
        if not (self.repo / ".git").exists():
            return []
        fmt = "%h%x1f%cI%x1f%s%x1f%b%x1e"
        out = self._git("log", f"-{limit}", f"--format={fmt}").stdout
        items = []
        for rec in filter(None, (r.strip() for r in out.split("\x1e"))):
            cid, ts, subject, body = (rec.split("\x1f") + [""] * 4)[:4]
            meta = dict(line.split(": ", 1) for line in body.splitlines() if ": " in line)
            items.append({"change": cid, "time": ts, "summary": subject, "by": meta.get("by", ""),
                          "undoes": meta.get("undoes", "")})
        return items

    def preview(self, new: Profile) -> str:
        return self._diff(self._planned_files(self.profile()), self._planned_files(new))

    # -- changing

    def init(self, profile: Profile | None = None, by: str = "setup") -> Result:
        """Create the repo and apply the first profile (the default, or the one given)."""
        if refused := self._refuse():
            return refused
        self.repo.mkdir(parents=True, exist_ok=True)
        if not (self.repo / ".git").exists():
            self._git("init", "-q", "-b", "main")
        return self.apply(profile or self.profile(), INIT_SUMMARY, by=by, force=True)

    def apply(self, new: Profile, summary: str, *, by: str = "exo", undoes: str = "",
              force: bool = False) -> Result:
        new.validate()
        try:
            new.check_roundtrip()  # never write a machine.toml that cannot be read back
        except ProfileError as exc:
            return Result(False, f"Not changed: {exc}")
        from .sandbox.desktop import check_profile, required
        from .sandbox.policy import SandboxError

        if required():
            try:
                check_profile(new)
            except SandboxError as exc:
                return Result(False, f"Not changed: {exc}")
        if refused := self._refuse():
            return refused
        if not (self.repo / ".git").exists() and summary != INIT_SUMMARY:
            first = self.init(by=by)  # record the starting point, so the first real change can be undone
            if not first.ok:
                return first
        with self._lock():
            if not (self.repo / ".git").exists():
                self.repo.mkdir(parents=True, exist_ok=True)
                self._git("init", "-q", "-b", "main")
            old_files = self._planned_files(self.profile()) if (self.repo / "machine.toml").exists() else {}
            new_files = self._planned_files(new)
            diff = self._diff(old_files, new_files)
            if not diff and not force:
                return Result(True, "Nothing to change.")
            proposed = self.ledger.append("change.proposed", {"summary": summary, "by": by, "diff": diff,
                                                              "undoes": undoes})
            backup = self._snapshot(new_files)
            self._write(new_files)
            notes: list[str] = []
            for check in self.checks:
                try:
                    ok, note = check(self.config_home, new)
                except Exception as e:  # a crashing check is a failed check
                    ok, note = False, f"{getattr(check, '__name__', 'check')} crashed: {e}"
                notes.append(note)
                if not ok:
                    self._restore(backup)
                    for again in self.checks:  # bring the live session back to the old files
                        try:
                            again(self.config_home, None)
                        except Exception:
                            pass
                    self.ledger.append("change.reverted", {"summary": summary, "by": by, "check": note,
                                                           "proposed": proposed})
                    return Result(False, "That did not work, so I put it back.", diff=diff, checks=notes,
                                  reverted=True)
            self._git("add", "-A")
            body = f"by: {by}" + (f"\nundoes: {undoes}" if undoes else "")
            self._git("commit", "-q", "--allow-empty", "-m", summary, "-m", body)
            change = self._git("rev-parse", "--short", "HEAD").stdout.strip()
            self.ledger.append("change.applied", {"summary": summary, "by": by, "change": change, "diff": diff,
                                                  "undoes": undoes, "checks": notes, "proposed": proposed})
            return Result(True, f"{summary}. Undo?", change=change, diff=diff, checks=notes)

    def undo(self, change: str | None = None, *, by: str = "exo") -> Result:
        """Put the profile back to how it was before `change` (default: the latest change)."""
        hist = self.history(200)
        if len(hist) < 2:
            return Result(False, "There is nothing to undo.")
        target = hist[0] if change is None else next((h for h in hist if h["change"].startswith(change)), None)
        if target is None:
            return Result(False, f"I could not find change {change}.")
        parent = self._git("rev-parse", "--short", f"{target['change']}^", check=False)
        if parent.returncode != 0:
            return Result(False, "That was the first setup; there is nothing before it.")
        old = Profile.loads(self._git("show", f"{parent.stdout.strip()}:machine.toml").stdout)
        return self.apply(old, f"Undo: {target['summary']}", by=by, undoes=target["change"])

    def redeploy(self) -> Result:
        """Write the current rendered files again (after a person or a package touched them)."""
        return self.apply(self.profile(), "Put the desktop config back as the profile says", force=True)

    # -- internals

    def _refuse(self) -> Result | None:
        """Nothing is written, checked or reloaded unless this desktop is an Exo OS one."""
        if self.owns(self.config_home):
            return None
        return Result(False, NOT_OURS, checks=[f"{self.config_home / 'hypr/hyprland.lua'} does not load {DESKTOP_MARK}"])

    def _planned_files(self, profile: Profile) -> dict[str, str]:
        files = {"machine.toml": profile.dumps()}
        files.update({f"rendered/{k}": v for k, v in self.renderer(profile).items()})
        return files

    def _targets(self, files: dict[str, str]) -> Iterator[tuple[Path, Path, str]]:
        for rel, text in files.items():
            yield self.repo / rel, (self.config_home / rel[len("rendered/"):] if rel.startswith("rendered/")
                                    else None), text

    def _snapshot(self, files: dict[str, str]) -> dict[Path, bytes | None]:
        """Bytes of every file about to be written (None: it did not exist), in the repo and deployed."""
        backup: dict[Path, bytes | None] = {}
        paths = [p for repo_p, deployed, _ in self._targets(files) for p in (repo_p, deployed) if p]
        paths += [p for p in (self.repo / "rendered").rglob("*") if p.is_file()] if (self.repo / "rendered").exists() else []
        for p in paths:
            backup[p] = p.read_bytes() if p.exists() else None
        return backup

    def _write(self, files: dict[str, str]) -> None:
        keep = set()
        for repo_p, deployed, text in self._targets(files):
            for p in filter(None, (repo_p, deployed)):
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + ".exo-os-tmp")
                tmp.write_text(text)
                os.replace(tmp, p)
            keep.add(repo_p)
        # A renderer that stops producing a file: drop it from the repo copy and from the config folder.
        if (self.repo / "rendered").exists():
            for p in list((self.repo / "rendered").rglob("*")):
                if p.is_file() and p not in keep:
                    p.unlink()
                    deployed = self.config_home / p.relative_to(self.repo / "rendered")
                    if deployed.exists():
                        deployed.unlink()

    def _restore(self, backup: dict[Path, bytes | None]) -> None:
        for p, data in backup.items():
            if data is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)

    @staticmethod
    def _diff(old: dict[str, str], new: dict[str, str]) -> str:
        out = []
        for rel in sorted(set(old) | set(new)):
            a, b = old.get(rel, ""), new.get(rel, "")
            if a != b:
                out += difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{rel}", f"b/{rel}")
        return "".join(out)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, **GIT_ENV}
        return subprocess.run(["git", "-C", str(self.repo), "-c", "commit.gpgsign=false", *args],
                              capture_output=True, text=True, env=env, check=check)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.repo.mkdir(parents=True, exist_ok=True)
        with open(self.repo.parent / f".{self.repo.name}.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

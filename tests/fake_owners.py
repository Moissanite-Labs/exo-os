"""Distinct file owners for unprivileged host tests of root provisioning code.

A host test cannot chown, so every file it creates belongs to the test user, and a
check that root owns one file while a service account owns another never runs.
FakeOwners records the owner each inode would have on the image instead: root for
whatever root creates, the service account for whatever appears while code runs
under its UID drop, and whatever os.chown sets. Path.lstat then reports that owner
for every path under the test root. Deletions drop the record, so a reused inode
number starts as root's again.

Inside run_as, the fake also refuses what the kernel refuses the dropped UID. An
audit hook checks each absolute path below the test root that the action opens,
lists, creates, links, removes, renames or chmods. It applies the recorded owners
and the real mode bits: search on every directory on the way, read or write on the
file, and write on the parent for a new or removed entry. A denied SQLite open
raises the error SQLite gives, and any other denied call raises PermissionError.
The test root stands in for system directories and is not checked itself. Calls
through a directory descriptor or a relative path are not checked.
"""
from __future__ import annotations

import errno
import gc
import os
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = (0, 0)
SEARCH, WRITE, READ = 1, 2, 4
_DROPS: list[_Drop] = []  # active run_as windows, innermost last


class _Denied(Exception):
    def __init__(self, path, code=errno.EACCES):
        super().__init__(path)
        self.path, self.code = path, code


def _audit(event, args):
    if not _DROPS or _DROPS[-1].busy:
        return
    drop = _DROPS[-1]
    drop.busy = True
    try:
        drop.check(event, args)
    except _Denied as denied:
        if event == "sqlite3.connect":
            raise sqlite3.OperationalError("unable to open database file") from None
        raise PermissionError(denied.code, os.strerror(denied.code), denied.path) from None
    finally:
        drop.busy = False


sys.addaudithook(_audit)


class FakeOwners:
    def __init__(self, root: Path, monkeypatch):
        self.roots = {os.fspath(root), os.path.realpath(root)}
        self.walk_root = os.path.realpath(root)
        self.owners: dict[tuple[int, int], tuple[int, int]] = {}
        real = {name: getattr(os, name) for name in ("unlink", "remove", "rmdir", "rename", "replace")}
        lstat = Path.lstat

        def faked_lstat(path):
            info = lstat(path)
            if not self._inside(path):
                return info
            values = list(info)
            values[4], values[5] = self.owner(info)
            return os.stat_result(values)

        def chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
            info = os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
            old = self.owners.get((info.st_dev, info.st_ino), ROOT)
            self.owners[(info.st_dev, info.st_ino)] = (old[0] if uid == -1 else uid, old[1] if gid == -1 else gid)

        def dropping(name):
            def call(path, *, dir_fd=None):
                info = _lstat_or_none(path, dir_fd)
                result = real[name](path, dir_fd=dir_fd)
                if info is not None and (name == "rmdir" or info.st_nlink <= 1):
                    self.owners.pop((info.st_dev, info.st_ino), None)
                return result
            return call

        def moving(name):
            def call(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
                moved, replaced = _lstat_or_none(src, src_dir_fd), _lstat_or_none(dst, dst_dir_fd)
                result = real[name](src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
                if (moved is not None and replaced is not None and replaced.st_nlink <= 1
                        and (replaced.st_dev, replaced.st_ino) != (moved.st_dev, moved.st_ino)):
                    self.owners.pop((replaced.st_dev, replaced.st_ino), None)
                return result
            return call

        monkeypatch.setattr(Path, "lstat", faked_lstat)
        monkeypatch.setattr(os, "chown", chown)
        for name in ("unlink", "remove", "rmdir"):
            monkeypatch.setattr(os, name, dropping(name))
        for name in ("rename", "replace"):
            monkeypatch.setattr(os, name, moving(name))

    def _inside(self, path) -> bool:
        text = os.fspath(path)
        return any(text == root or text.startswith(root + os.sep) for root in self.roots)

    def _inodes(self) -> set[tuple[int, int]]:
        found = set()
        for top, dirs, files in os.walk(self.walk_root):
            for name in (os.curdir, *dirs, *files):
                info = os.lstat(os.path.join(top, name))
                found.add((info.st_dev, info.st_ino))
        return found

    def owner(self, info) -> tuple[int, int]:
        """The image owner of an inode: recorded, the dropped UID's if new in its window, or root's."""
        key = (info.st_dev, info.st_ino)
        if key in self.owners:
            return self.owners[key]
        drop = next((drop for drop in reversed(_DROPS) if drop.fake is self), None)
        if drop is not None and key not in drop.before:
            return drop.uid, drop.gid
        return ROOT

    def run_as(self, uid: int, gid: int, action):
        """Run action as if after a UID drop: every inode it creates belongs to uid:gid.

        The kernel's permission checks for uid:gid apply to the action, as described
        above. Like the forked helper it stands in for, it returns no live object, so
        SQLite handles opened by the action close, and their side files go, inside
        the window.
        """
        before = self._inodes()
        _DROPS.append(_Drop(self, uid, gid, before))
        try:
            action()
        finally:
            gc.collect()
            _DROPS.pop()
            after = self._inodes()
            for key in after - before:
                self.owners[key] = (uid, gid)
            for key in set(self.owners) - after:
                del self.owners[key]

    def set(self, path: Path, uid: int, gid: int) -> None:
        """Give one existing path another owner, as a misowned leftover would have."""
        info = os.lstat(path)
        self.owners[(info.st_dev, info.st_ino)] = (uid, gid)


class _Drop:
    """The discretionary access checks the kernel applies to one dropped uid:gid with no groups."""

    def __init__(self, fake: FakeOwners, uid: int, gid: int, before: set[tuple[int, int]]):
        self.fake, self.uid, self.gid, self.before, self.busy = fake, uid, gid, before, False

    def check(self, event, args):
        if event == "open":
            path, _, flags = args
            want = {os.O_RDONLY: READ, os.O_WRONLY: WRITE}.get(flags & os.O_ACCMODE, READ | WRITE)
            self._file(path, want | (WRITE if flags & os.O_TRUNC else 0), create=bool(flags & os.O_CREAT),
                       follow=not flags & os.O_NOFOLLOW)
        elif event == "sqlite3.connect":
            database, mode = args[0], "rwc"
            if isinstance(database, str) and database.startswith("file:"):
                parts = urlsplit(database)
                database, mode = unquote(parts.path), parse_qs(parts.query).get("mode", ["rwc"])[0]
            # SQLite opens a file it may only read read-only, so opening needs read alone.
            self._file(database, READ, create=mode == "rwc")
        elif event in ("os.listdir", "os.scandir"):
            self._file(args[0], READ)
        elif event in ("os.mkdir", "os.remove", "os.rmdir"):
            self._entry(args[0])
        elif event == "os.rename":
            self._entry(args[0])
            self._entry(args[1])
        elif event == "os.link":
            self._file(args[0], 0)
            self._entry(args[1])
        elif event == "os.symlink":
            self._entry(args[1])
        elif event == "os.chmod":
            text = self._below(args[0])
            info = self._stat(text) if text else None
            if info is not None and self.uid != 0 and self.fake.owner(info)[0] != self.uid:
                raise _Denied(text, errno.EPERM)

    def _below(self, value):
        """The absolute path below the test root that value names, or None."""
        if value is None or isinstance(value, int):
            return None
        text = os.fsdecode(value)
        if not os.path.isabs(text):
            return None
        text = os.path.normpath(text)
        return text if self.fake._inside(text) and text not in self.fake.roots else None

    def _may(self, info, want) -> bool:
        if self.uid == 0:
            return True
        uid, gid = self.fake.owner(info)
        shift = 6 if uid == self.uid else 3 if gid == self.gid else 0
        return (info.st_mode >> shift) & want == want

    def _stat(self, text, follow=True):
        try:
            return os.stat(text) if follow else os.lstat(text)
        except OSError:
            return None  # the real call reports a missing path

    def _reach(self, text):
        """Search on each directory below the test root on the way to text, as written and resolved."""
        parent = os.path.dirname(text)
        for path in (parent, os.path.realpath(parent)):
            for root in self.fake.roots:
                if not path.startswith(root + os.sep):
                    continue
                parts = path[len(root) + 1:].split(os.sep)
                for count in range(1, len(parts) + 1):
                    directory = os.path.join(root, *parts[:count])
                    info = self._stat(directory, follow=False)
                    if info is None:
                        break
                    if stat.S_ISDIR(info.st_mode) and not self._may(info, SEARCH):
                        raise _Denied(directory)

    def _file(self, value, want, *, create=False, follow=True):
        text = self._below(value)
        if text is None:
            return
        self._reach(text)
        info = self._stat(text, follow)
        if info is None:
            if create:
                self._entry(text)
        elif not self._may(info, want):
            raise _Denied(text)

    def _entry(self, value):
        """A new, removed or renamed name needs write and search on its directory."""
        text = self._below(value)
        if text is None:
            return
        self._reach(text)
        parent = os.path.realpath(os.path.dirname(text))
        info = self._stat(parent)
        if info is not None and self.fake._inside(parent) and parent not in self.fake.roots \
                and not self._may(info, WRITE | SEARCH):
            raise _Denied(parent)


def _lstat_or_none(path, dir_fd):
    try:
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except (OSError, TypeError, ValueError):
        return None

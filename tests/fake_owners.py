"""Distinct file owners for unprivileged host tests of root provisioning code.

A host test cannot chown, so every file it creates belongs to the test user, and a
check that root owns one file while a service account owns another never runs.
FakeOwners records the owner each inode would have on the image instead: root for
whatever root creates, the service account for whatever appears while code runs
under its UID drop, and whatever os.chown sets. Path.lstat then reports that owner
for every path under the test root. Deletions drop the record, so a reused inode
number starts as root's again.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path

ROOT = (0, 0)


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
            values[4], values[5] = self.owners.get((info.st_dev, info.st_ino), ROOT)
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

    def run_as(self, uid: int, gid: int, action):
        """Run action as if after a UID drop: every inode it creates belongs to uid:gid.

        Like the forked helper it stands in for, it returns no live object, so SQLite
        handles opened by the action close, and their side files go, inside the window.
        """
        before = self._inodes()
        try:
            action()
        finally:
            gc.collect()
            after = self._inodes()
            for key in after - before:
                self.owners[key] = (uid, gid)
            for key in set(self.owners) - after:
                del self.owners[key]

    def set(self, path: Path, uid: int, gid: int) -> None:
        """Give one existing path another owner, as a misowned leftover would have."""
        info = os.lstat(path)
        self.owners[(info.st_dev, info.st_ino)] = (uid, gid)


def _lstat_or_none(path, dir_fd):
    try:
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except (OSError, TypeError, ValueError):
        return None

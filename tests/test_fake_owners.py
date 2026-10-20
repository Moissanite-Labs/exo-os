"""Self-tests for FakeOwners: inside run_as it must refuse what a real UID drop refuses.

Every file here belongs to root on the simulated image, so the dropped UID is "other"
and only the world mode bits apply to it.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from fake_owners import FakeOwners

UID, GID = 4301, 4302


@pytest.fixture
def owners(tmp_path, monkeypatch):
    return FakeOwners(tmp_path, monkeypatch)


def refused(owners, action, code=errno.EACCES):
    with pytest.raises(PermissionError) as caught:
        owners.run_as(UID, GID, action)
    assert caught.value.errno == code


def allowed(owners, action):
    owners.run_as(UID, GID, action)


def make(path: Path, mode: int, kind="file") -> Path:
    if kind == "dir":
        path.mkdir()
    else:
        path.write_text("data")
    os.chmod(path, mode)
    return path


def read(path):
    return lambda: Path(path).read_text()


def write(path):
    return lambda: Path(path).write_text("new")


def test_open_for_reading_needs_read_permission(tmp_path, owners):
    closed = make(tmp_path / "closed", 0o600)
    opened = make(tmp_path / "opened", 0o604)
    refused(owners, read(closed))
    allowed(owners, read(opened))


def test_open_for_writing_needs_write_permission(tmp_path, owners):
    readonly = make(tmp_path / "readonly", 0o644)
    writable = make(tmp_path / "writable", 0o646)
    refused(owners, write(readonly))
    allowed(owners, write(writable))


def test_write_only_open_needs_write_without_truncation(tmp_path, owners):
    readonly = make(tmp_path / "readonly", 0o644)
    refused(owners, lambda: os.close(os.open(readonly, os.O_WRONLY)))
    refused(owners, lambda: os.close(os.open(readonly, os.O_WRONLY | os.O_APPEND)))


def test_open_for_reading_and_writing_needs_both(tmp_path, owners):
    only_read = make(tmp_path / "only_read", 0o644)
    refused(owners, lambda: open(only_read, "r+").close())


def test_truncating_open_needs_write_permission(tmp_path, owners):
    readonly = make(tmp_path / "readonly", 0o644)
    refused(owners, lambda: os.close(os.open(readonly, os.O_RDONLY | os.O_TRUNC)))
    assert readonly.read_text() == "data"


def test_open_needs_search_on_every_directory_on_the_way(tmp_path, owners):
    sealed = make(tmp_path / "sealed", 0o700, "dir")
    inner = sealed / "file"
    inner.write_text("data")
    os.chmod(inner, 0o666)
    refused(owners, read(inner))
    os.chmod(sealed, 0o711)
    allowed(owners, read(inner))


def test_creating_by_open_needs_write_on_the_parent(tmp_path, owners):
    shut = make(tmp_path / "shut", 0o755, "dir")
    refused(owners, write(shut / "new"))
    assert not (shut / "new").exists()
    os.chmod(shut, 0o777)
    allowed(owners, write(shut / "new"))


def test_listing_a_directory_needs_read_permission(tmp_path, owners):
    sealed = make(tmp_path / "sealed", 0o711, "dir")
    refused(owners, lambda: os.listdir(sealed))
    refused(owners, lambda: list(os.scandir(sealed)))
    os.chmod(sealed, 0o755)
    allowed(owners, lambda: os.listdir(sealed))
    allowed(owners, lambda: list(os.scandir(sealed)))


def test_chmod_needs_ownership(tmp_path, owners):
    target = make(tmp_path / "target", 0o666)
    refused(owners, lambda: os.chmod(target, 0o600), errno.EPERM)
    assert target.stat().st_mode & 0o777 == 0o666
    owners.set(target, UID, GID)
    allowed(owners, lambda: os.chmod(target, 0o600))
    assert target.stat().st_mode & 0o777 == 0o600


def test_changing_a_directory_needs_write_on_its_parent(tmp_path, owners):
    shut = make(tmp_path / "shut", 0o755, "dir")
    make(shut / "victim", 0o666)
    other = make(tmp_path / "other", 0o777, "dir")
    actions = {
        "mkdir": lambda: os.mkdir(shut / "made"),
        "remove": lambda: os.remove(shut / "victim"),
        "unlink": lambda: os.unlink(shut / "victim"),
        "rename out": lambda: os.rename(shut / "victim", other / "moved"),
        "rename in": lambda: os.rename(other / "item", shut / "moved"),
        "link": lambda: os.link(other / "item", shut / "linked"),
        "symlink": lambda: os.symlink("target", shut / "symlinked"),
    }
    (other / "item").write_text("data")
    for name, action in actions.items():
        refused(owners, action)
        assert sorted(path.name for path in shut.iterdir()) == ["victim"], name
    assert (other / "item").exists()
    os.chmod(shut, 0o777)
    for name in ("mkdir", "link", "symlink", "rename in", "remove"):
        allowed(owners, actions[name])
    assert sorted(path.name for path in shut.iterdir()) == ["linked", "made", "moved", "symlinked"]


def test_removing_a_directory_needs_write_on_its_parent(tmp_path, owners):
    shut = make(tmp_path / "shut", 0o755, "dir")
    (shut / "empty").mkdir()
    refused(owners, lambda: os.rmdir(shut / "empty"))
    os.chmod(shut, 0o777)
    allowed(owners, lambda: os.rmdir(shut / "empty"))


def test_owned_parent_is_writable_through_owner_bits(tmp_path, owners):
    mine = make(tmp_path / "mine", 0o700, "dir")
    refused(owners, write(mine / "new"))
    owners.set(mine, UID, GID)
    allowed(owners, write(mine / "new"))

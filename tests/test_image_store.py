"""Exercise the final postinstall store check without building or booting an image."""
import os
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
POSTINSTALL = PROJECT / "image/mkosi.postinst.chroot"


def finalize_store(store, monkeypatch, *, untrusted_owner=None):
    source = POSTINSTALL.read_text()
    block = source.split("/opt/exo/bin/python - <<'STORE'\n", 1)[1].split("\nSTORE", 1)[0]
    assert block.count('store = Path("/var/lib/exo")') == 1
    block = block.replace('store = Path("/var/lib/exo")', f"store = Path({str(store)!r})")
    # mkosi runs postinstall as root in its image namespace. Model that ownership
    # on host fixtures while retaining the real file types, modes and contents.
    real_lstat = Path.lstat

    def image_lstat(path):
        fields = list(real_lstat(path))
        fields[4] = 1000 if path == untrusted_owner else 0
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", image_lstat)
    exec(compile(block, str(POSTINSTALL), "exec"), {})


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "store"
    path.mkdir(mode=0o755)
    (path / "exo").mkdir(mode=0o700)
    return path


@pytest.mark.parametrize("placeholder", [False, True])
def test_postinstall_leaves_only_empty_principal_skeleton(store, monkeypatch, placeholder):
    if placeholder:
        (store / ".keep").touch()
        (store / ".keep").chmod(0o644)
    finalize_store(store, monkeypatch)
    assert list(store.iterdir()) == [store / "exo"]
    assert not list((store / "exo").iterdir())
    assert (store / "exo").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("artifact", ["empty", "file", "populated", "symlink", "dangling_symlink"])
def test_postinstall_rejects_unexpected_root_artifact(store, monkeypatch, tmp_path, artifact):
    root = store / "root"
    target = tmp_path / "target"
    target.mkdir()
    if artifact == "empty":
        root.mkdir()
    elif artifact == "file":
        root.touch()
    elif artifact == "populated":
        root.mkdir()
        (root / "data").write_text("preserve")
    else:
        root.symlink_to(target if artifact == "symlink" else tmp_path / "missing")
    with pytest.raises((RuntimeError, OSError)):
        finalize_store(store, monkeypatch)
    assert root.exists() or root.is_symlink()
    assert target.is_dir()
    if artifact == "populated":
        assert (root / "data").read_text() == "preserve"


@pytest.mark.parametrize("artifact", ["populated", "directory", "symlink", "owner",
                                    "group_writable", "other_writable", "unexpected_mode"])
def test_postinstall_rejects_unexpected_placeholder(store, monkeypatch, artifact):
    placeholder = store / ".keep"
    if artifact == "populated":
        placeholder.write_text("preserve")
    elif artifact == "directory":
        placeholder.mkdir()
    elif artifact == "symlink":
        placeholder.symlink_to(store / "missing")
    else:
        placeholder.touch()
        placeholder.chmod({"owner": 0o644, "group_writable": 0o664,
                           "other_writable": 0o646, "unexpected_mode": 0o600}[artifact])
    with pytest.raises(RuntimeError, match="placeholder"):
        finalize_store(store, monkeypatch, untrusted_owner=placeholder if artifact == "owner" else None)
    assert placeholder.exists() or placeholder.is_symlink()


@pytest.mark.parametrize("problem", ["extra", "populated", "symlink", "writable", "owner", "missing"])
def test_postinstall_rejects_invalid_fresh_store(store, monkeypatch, problem):
    skeleton = store / "exo"
    if problem == "extra":
        (store / "unexpected").touch()
    elif problem == "populated":
        (skeleton / "data").touch()
    elif problem == "symlink":
        skeleton.rmdir()
        skeleton.symlink_to(store.parent)
    elif problem == "writable":
        skeleton.chmod(0o770)
    elif problem == "missing":
        skeleton.rmdir()
    with pytest.raises(RuntimeError):
        finalize_store(store, monkeypatch, untrusted_owner=skeleton if problem == "owner" else None)


@pytest.mark.parametrize("problem", ["symlink", "writable", "owner"])
def test_postinstall_rejects_untrusted_store_directory(store, monkeypatch, problem):
    path = store
    if problem == "symlink":
        path = store.parent / "store-link"
        path.symlink_to(store)
    elif problem == "writable":
        store.chmod(0o775)
    with pytest.raises(RuntimeError, match="root-owned"):
        finalize_store(path, monkeypatch, untrusted_owner=path if problem == "owner" else None)


def test_store_contract_is_final_postinstall_action_before_repart():
    source = POSTINSTALL.read_text()
    assert source.rstrip().endswith("STORE")
    assert source.index("install -d -m 0700 /var/lib/exo/exo") < source.index("<<'STORE'")
    assert "CopyFiles=/var/lib/exo:/@store" in (PROJECT / "image/mkosi.repart/10-root.conf").read_text()

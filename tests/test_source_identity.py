"""Physical source-root checks use directory identity, not pathname spelling."""
import importlib.util
from pathlib import Path
import subprocess

import pytest


HELPER = Path(__file__).resolve().parents[1] / 'tools/source_identity.py'
spec = importlib.util.spec_from_file_location('source_identity', HELPER)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


def git_repo(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    return path


def test_case_alias_of_the_same_physical_git_root(tmp_path):
    root = git_repo(tmp_path / 'CaseRepo')
    alias = tmp_path / 'caserepo'
    if not alias.exists() or not alias.samefile(root):
        pytest.skip('filesystem does not expose case aliases of one directory')
    assert alias.resolve() != root.resolve()
    assert identity.git_root(root)
    assert identity.git_root(alias)


def test_git_reported_physical_alias_is_accepted(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'repo')
    alias = tmp_path / 'git-reported-root'
    alias.symlink_to(root, target_is_directory=True)
    # Simulate a Git-reported alternate name using two real directory paths.
    # Compare real filesystem aliases rather than supplying an equality result.
    monkeypatch.setattr(identity.subprocess, 'check_output', lambda *a, **kw: str(alias) + '\n')
    assert alias.samefile(root)
    assert identity.git_root(root)


def test_different_physical_git_reported_root_is_refused(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'repo')
    other = git_repo(tmp_path / 'other')
    assert not root.samefile(other)
    monkeypatch.setattr(identity.subprocess, 'check_output', lambda *a, **kw: str(other) + '\n')
    assert not identity.git_root(root)


def test_nested_directory_never_borrows_enclosing_git_head(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'repo')
    child = root / 'archive'
    child.mkdir()
    monkeypatch.delenv('EXO_OS_SOURCE_COMMIT', raising=False)
    assert identity.git_root(root)
    assert not identity.git_root(child)
    with pytest.raises(ValueError, match='identity is missing'):
        identity.source_identity(child)
    (child / '.EXO_OS_SOURCE_ID').write_text('a' * 40 + '\n')
    assert identity.source_identity(child) == 'a' * 40


def test_missing_source_root_is_refused(tmp_path):
    assert not identity.git_root(tmp_path / 'missing')


def test_physical_root_still_requires_clean_committed_sources(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'repo')
    payload = root / 'payload'
    payload.write_text('committed\n')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    monkeypatch.delenv('EXO_OS_SOURCE_COMMIT', raising=False)
    expected = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    assert identity.source_identity(root, require_clean=True) == expected
    payload.write_text('changed\n')
    with pytest.raises(ValueError, match='clean committed checkout'):
        identity.source_identity(root, require_clean=True)


@pytest.mark.parametrize('missing', ['source', 'git_top'])
def test_missing_directory_during_physical_comparison_is_refused(tmp_path, monkeypatch, missing):
    root = git_repo(tmp_path / 'repo')
    top = root
    if missing == 'source':
        source = tmp_path / 'missing'
    else:
        source = root
        top = tmp_path / 'missing'
    monkeypatch.setattr(identity.subprocess, 'check_output', lambda *a, **kw: str(top) + '\n')
    assert not identity.git_root(source)


def test_physical_comparison_oserror_is_refused(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'repo')
    def inaccessible(left, right):
        raise PermissionError('directory identity unavailable')
    monkeypatch.setattr(Path, 'samefile', inaccessible)
    assert not identity.git_root(root)

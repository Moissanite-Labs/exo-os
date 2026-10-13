"""Host fixtures only: no image build or QEMU execution."""
import importlib.util
import json
import os
import py_compile
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), TOOLS / f'{name}.py')
    loaded = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(TOOLS))
    try:
        spec.loader.exec_module(loaded)
    finally:
        sys.path.pop(0)
    return loaded


def source_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    for tree in ('src', 'image', 'tools'):
        (root / tree).mkdir(exist_ok=True)
    (root / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    (root / 'src/fixture.py').write_text('VALUE=1\n')
    (root / 'image/mkosi.conf').write_text('[Output]\nFormat=disk\n')
    for name in ('build-image.sh', 'build-provenance.py', 'build-profile.py', 'env.sh',
                 'source_identity.py', 'build-receipt.py'):
        if (TOOLS / name).exists():
            shutil.copy2(TOOLS / name, root / 'tools' / name)
    (root / '.EXO_OS_SOURCE_ID').write_text('a' * 40 + '\n')
    return root


@pytest.mark.parametrize('args', [['-fC/tmp/other'], ['-wC', '/tmp/other'], ['-fI', '/tmp/x'],
                                   ['--bogus-flag'], ['--tools-tree-pro=runtime'], ['--force']])
def test_wrapper_allows_only_documented_options(args):
    with pytest.raises(ValueError):
        module('build-profile').selected_profile(['--profile=vmtest', *args])


@pytest.mark.parametrize('path', ['image/mkosi.cache/entry', 'image/mkosi.builddir/entry',
                                  'image/mkosi.tools/entry', 'image/mkosi.tools.manifest',
                                  'image/out/private/artifact', 'src/__pycache__/x.pyc'])
def test_generated_paths_do_not_change_digest(tmp_path, path):
    root = source_fixture(tmp_path)
    digest = module('build-provenance').source_digest
    before = digest(root)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('generated')
    assert digest(root) == before


@pytest.mark.parametrize('name', ['build-image.sh', 'build-provenance.py', 'build-profile.py', 'env.sh',
                                  'source_identity.py', 'build-receipt.py'])
def test_builder_bytes_change_digest(tmp_path, name):
    root = source_fixture(tmp_path)
    digest = module('build-provenance').source_digest
    before = digest(root)
    with (root / 'tools' / name).open('a') as stream:
        stream.write('\n# changed\n')
    assert digest(root) != before


def test_acceptance_tool_bytes_do_not_change_digest(tmp_path):
    root = source_fixture(tmp_path)
    digest = module('build-provenance').source_digest
    before = digest(root)
    (root / 'tools/accept-phase3.sh').write_text('# tools-only change\n')
    assert digest(root) == before


def test_nested_archive_uses_own_source_id(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'outer', '--allow-empty'], check=True)
    root = source_fixture(tmp_path / 'archive')
    manifest = root / 'image/out/build.json'
    env = {k: v for k, v in os.environ.items() if not k.startswith('EXO_')}
    r = subprocess.run([sys.executable, str(root / 'tools/build-provenance.py'), '--source-root',
                        str(root), '--pin', 'b' * 40, '--output', str(manifest)], env=env, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(manifest.read_text())['exo_os_commit'] == 'a' * 40


@pytest.mark.parametrize('local', ['mkosi.local.conf', 'mkosi.local'])
@pytest.mark.parametrize('dangling', [False, True])
def test_wrapper_refuses_local_config_before_any_build_tool(tmp_path, local, dangling):
    root = source_fixture(tmp_path)
    target = root / 'image' / local
    if dangling:
        target.symlink_to(tmp_path / 'missing')
    elif local.endswith('.conf'):
        target.write_text('[Config]\nProfiles=vmtest\n')
    else:
        target.mkdir()
    r = subprocess.run(['bash', str(root / 'tools/build-image.sh')], capture_output=True, text=True,
                        env={**os.environ, 'EXO_OS_TOOLS': str(tmp_path / 'no-tools')})
    assert r.returncode == 2 and 'mkosi.local' in r.stderr


def mock_build_fixture(tmp_path):
    root = source_fixture(tmp_path / 'project')
    (root / 'exo.pin').write_text('b' * 40 + '\n')
    core = root / '.deps/exo'
    for name in ('exo-gate', 'exo-memory', 'exo-context'):
        target = core / 'plugins' / name
        target.mkdir(parents=True)
        (target / 'plugin.py').write_text('# synthetic trusted archive\n')
    (core / '.EXO_PIN').write_text('b' * 40 + '\n')
    plugin = root / 'image/mkosi.extra/opt/exo/plugins/exo-os'
    plugin.mkdir(parents=True)
    (plugin / 'plugin.py').write_text('# fixture\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name in ('uv', 'uvx'):
        script = bin_dir / name
        script.write_text('#!/bin/sh\nexit 0\n')
        script.chmod(0o755)
    mkosi = bin_dir / 'mock-mkosi'
    mkosi.write_text('''#!/usr/bin/env bash
set -eu
for arg in "$@"; do
  case "$arg" in --output-directory=*) output=${arg#*=};; esac
done
printf 'synthetic raw for %s' "$*" > "$output/exo-os.raw"
exit "${MOCK_MKOSI_STATUS:-0}"
''')
    mkosi.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith('EXO_') and k != 'PYTHONPATH'}
    env.update(PATH=str(bin_dir) + ':' + env['PATH'], EXO_OS_MKOSI=str(mkosi),
               EXO_OS_TOOLS=str(tmp_path / 'absent-tools'), XDG_CACHE_HOME=str(tmp_path / 'cache'))
    return root, env


def build(root, env, *, vmtest=False, status=0):
    return subprocess.run(['bash', str(root / 'tools/build-image.sh'),
                           *(['--profile=vmtest'] if vmtest else [])],
                          env={**env, 'MOCK_MKOSI_STATUS': str(status)},
                          capture_output=True, text=True, timeout=10)


def accept_without_qemu(root, env):
    python = root / '.venv/bin/python'
    python.parent.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        python.symlink_to(sys.executable)
    runner = root / 'tools/run-vm.sh'
    runner.write_text('#!/bin/sh\ntouch "$(dirname "$0")/../QEMU_WAS_STARTED"\nexit 1\n')
    runner.chmod(0o755)
    script = '''set -eu
here=$1
out="$here/image/out"
report="$out/report"
result() { echo "$1: $3"; }
ssh_vm() { echo UNEXPECTED_SSH; }
source "$2"
accept_start 1 1024
'''
    return subprocess.run(['bash', '-c', script, 'fixture', str(root), str(TOOLS / 'accept-common.sh')],
                          env=env, capture_output=True, text=True, timeout=5)


def test_failed_mkosi_keeps_production_pair_and_prevents_qemu(tmp_path):
    root, env = mock_build_fixture(tmp_path)
    assert build(root, env).returncode == 0
    out = root / 'image/out'
    old_raw = (out / 'exo-os.raw').read_bytes()
    old_manifest = (out / 'extra/opt/exo/EXO_OS_BUILD.json').read_bytes()
    failed = build(root, env, vmtest=True, status=9)
    assert failed.returncode == 9, failed.stderr
    assert (out / 'exo-os.raw').read_bytes() == old_raw
    assert (out / 'extra/opt/exo/EXO_OS_BUILD.json').read_bytes() == old_manifest
    assert not (out / 'exo-os-build-success.json').exists()
    assert list(out.glob('.build-attempt.*/prior-success.json'))
    # Reproduce the review's stale production raw/new vmtest manifest pairing.
    manifest = json.loads(old_manifest)
    manifest['build_profile'] = 'vmtest'
    (out / 'extra/opt/exo/EXO_OS_BUILD.json').write_text(json.dumps(manifest))
    accepted = accept_without_qemu(root, env)
    assert accepted.returncode != 0 and 'successful-build receipt' in accepted.stdout
    assert not (root / 'QEMU_WAS_STARTED').exists()
    assert not (out / 'qemu.log').exists()
    assert (out / 'exo-os.raw').read_bytes() == old_raw


def test_successful_build_publishes_bound_pair_and_keeps_history(tmp_path):
    root, env = mock_build_fixture(tmp_path)
    first = build(root, env)
    assert first.returncode == 0, first.stderr
    out = root / 'image/out'
    old_raw = (out / 'exo-os.raw').read_bytes()
    old_manifest = (out / 'extra/opt/exo/EXO_OS_BUILD.json').read_bytes()
    old_receipt = (out / 'exo-os-build-success.json').read_bytes()
    second = build(root, env, vmtest=True)
    assert second.returncode == 0, second.stderr
    history = next(p for p in (out / 'build-history').iterdir() if (p / 'exo-os.raw').exists())
    assert (history / 'exo-os.raw').read_bytes() == old_raw
    assert (history / 'extra/opt/exo/EXO_OS_BUILD.json').read_bytes() == old_manifest
    assert (history / 'exo-os-build-success.json').read_bytes() == old_receipt
    receipt = module('build-receipt')
    receipt.verify(out, out / 'extra/opt/exo/EXO_OS_BUILD.json')
    assert not (out / '.build-lock').exists()
    # .EXO_PIN is an operator assertion. The receipt must not call it content authentication.
    assert set(json.loads((out / 'exo-os-build-success.json').read_text())) == {
        'schema', 'raw_sha256', 'manifest_sha256', 'exo_os_commit', 'source_sha256', 'build_profile', 'exo_pin'}


@pytest.mark.parametrize('artifact', ['exo-os.raw', 'extra/opt/exo/EXO_OS_BUILD.json',
                                     'exo-os-build-success.json'])
def test_changed_published_artifact_prevents_qemu(tmp_path, artifact):
    root, env = mock_build_fixture(tmp_path)
    assert build(root, env, vmtest=True).returncode == 0
    with (root / 'image/out' / artifact).open('ab') as stream:
        stream.write(b'changed')
    accepted = accept_without_qemu(root, env)
    assert accepted.returncode != 0
    assert not (root / 'QEMU_WAS_STARTED').exists()
    assert not (root / 'image/out/qemu.log').exists()


@pytest.mark.parametrize('failure', [1, 2, 3, 4, 5, 6])
def test_publication_interruption_never_leaves_valid_receipt(tmp_path, monkeypatch, failure):
    root, env = mock_build_fixture(tmp_path)
    assert build(root, env, vmtest=True).returncode == 0
    out = root / 'image/out'
    stage = out / '.build-attempt.interruption'
    stage.mkdir()
    shutil.copy2(out / 'exo-os.raw', stage / 'exo-os.raw')
    shutil.copytree(out / 'extra', stage / 'extra')
    shutil.copy2(out / 'exo-os-build-success.json', stage / 'prior-success.json')
    prior_bytes = (stage / 'prior-success.json').read_bytes()
    receipt = module('build-receipt')
    rename, replace = Path.rename, receipt.os.replace
    calls = 0
    def step():
        nonlocal calls
        calls += 1
        if calls == failure:
            raise OSError('synthetic publication failure')
    def interrupted_rename(self, target):
        step()
        return rename(self, target)
    def interrupted_replace(source, target):
        step()
        return replace(source, target)
    monkeypatch.setattr(Path, 'rename', interrupted_rename)
    monkeypatch.setattr(receipt.os, 'replace', interrupted_replace)
    with pytest.raises(OSError, match='synthetic publication failure'):
        receipt.publish(stage, out)
    assert not (out / 'exo-os-build-success.json').exists()
    preserved = list((out / 'build-history').glob('*/exo-os-build-success.json'))
    if (stage / 'prior-success.json').exists():
        preserved.append(stage / 'prior-success.json')
    assert any(path.read_bytes() == prior_bytes for path in preserved)
    accepted = accept_without_qemu(root, env)
    assert accepted.returncode != 0
    assert not (root / 'QEMU_WAS_STARTED').exists()


@pytest.mark.parametrize('value', ['', 'short', 'A' * 40, 'b' * 40])
def test_source_identity_rejects_malformed_or_contradictory_assertion(tmp_path, monkeypatch, value):
    root = source_fixture(tmp_path)
    monkeypatch.setenv('EXO_OS_SOURCE_COMMIT', value)
    with pytest.raises(ValueError):
        module('source_identity').source_identity(root)


def test_source_identity_explicit_fallback_and_agreeing_marker(tmp_path, monkeypatch):
    root = source_fixture(tmp_path)
    monkeypatch.setenv('EXO_OS_SOURCE_COMMIT', 'a' * 40)
    identity = module('source_identity').source_identity
    assert identity(root) == 'a' * 40
    (root / '.EXO_OS_SOURCE_ID').unlink()
    assert identity(root) == 'a' * 40


def test_clean_git_identity_through_symlink_and_contradictory_marker(tmp_path, monkeypatch):
    monkeypatch.delenv('EXO_OS_SOURCE_COMMIT', raising=False)
    root = source_fixture(tmp_path / 'repo')
    (root / '.EXO_OS_SOURCE_ID').unlink()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    link = tmp_path / 'linked-repo'
    link.symlink_to(root, target_is_directory=True)
    identity = module('source_identity').source_identity
    assert identity(link, require_clean=True) == commit
    (root / '.EXO_OS_SOURCE_ID').write_text('b' * 40)
    with pytest.raises(ValueError, match='contradictory'):
        identity(link)


@pytest.mark.parametrize('kind', ['directory', 'symlink', 'dangling', 'fifo', 'oversized',
                                  'whitespace', 'extra_newline', 'non_ascii'])
def test_source_marker_rejects_bad_type_size_or_encoding(tmp_path, monkeypatch, kind):
    root = source_fixture(tmp_path)
    monkeypatch.delenv('EXO_OS_SOURCE_COMMIT', raising=False)
    marker = root / '.EXO_OS_SOURCE_ID'
    marker.unlink()
    if kind == 'directory':
        marker.mkdir()
    elif kind in ('symlink', 'dangling'):
        target = tmp_path / 'target'
        if kind == 'symlink':
            target.write_text('a' * 40 + '\n')
        marker.symlink_to(target)
    elif kind == 'fifo':
        os.mkfifo(marker)
    else:
        data = {'oversized': b'a' * 65, 'whitespace': b' ' + b'a' * 40,
                'extra_newline': b'a' * 40 + b'\n\n', 'non_ascii': b'\xff' * 40}[kind]
        marker.write_bytes(data)
    run = subprocess.run([sys.executable, str(root / 'tools/source_identity.py'),
                          '--source-root', str(root)], capture_output=True, text=True, timeout=3)
    assert run.returncode == 2 and '.EXO_OS_SOURCE_ID' in run.stderr


@pytest.mark.parametrize('value', [' ' + 'a' * 40, 'a' * 40 + '\n'])
def test_explicit_environment_identity_is_strict(tmp_path, monkeypatch, value):
    root = source_fixture(tmp_path)
    monkeypatch.setenv('EXO_OS_SOURCE_COMMIT', value)
    with pytest.raises(ValueError, match='40-hex'):
        module('source_identity').source_identity(root)


def commit_source(root):
    (root / '.EXO_OS_SOURCE_ID').unlink()
    (root / '.gitignore').write_text('image/out/\n.deps/\n.venv/\n__pycache__/\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)


def test_dirty_git_build_refused_before_receipt_or_artifact_changes(tmp_path):
    root, env = mock_build_fixture(tmp_path)
    commit_source(root)
    successful = build(root, env, vmtest=True)
    assert successful.returncode == 0, successful.stderr
    out = root / 'image/out'
    names = ('exo-os.raw', 'extra/opt/exo/EXO_OS_BUILD.json', 'exo-os-build-success.json')
    before = {name: (out / name).read_bytes() for name in names}
    attempts = set(out.glob('.build-attempt.*'))
    (root / 'src/fixture.py').write_text('VALUE=2\n')
    rejected = build(root, env, vmtest=True)
    assert rejected.returncode == 2 and 'clean committed checkout' in rejected.stderr
    assert {name: (out / name).read_bytes() for name in names} == before
    assert set(out.glob('.build-attempt.*')) == attempts
    assert not (out / '.build-lock').exists()


@pytest.mark.parametrize('failed_flush', ['receipt', 'directory', 'directory_open'])
def test_publication_flush_failure_keeps_old_receipt_and_blocks_acceptance(tmp_path, monkeypatch, failed_flush):
    root, env = mock_build_fixture(tmp_path)
    assert build(root, env, vmtest=True).returncode == 0
    out = root / 'image/out'
    stage = out / '.build-attempt.flush-failure'
    stage.mkdir()
    shutil.copy2(out / 'exo-os.raw', stage / 'exo-os.raw')
    shutil.copytree(out / 'extra', stage / 'extra')
    shutil.copy2(out / 'exo-os-build-success.json', stage / 'prior-success.json')
    prior_bytes = (stage / 'prior-success.json').read_bytes()
    receipt = module('build-receipt')
    real_fsync, real_open = receipt.os.fsync, receipt.os.open
    flushes = []
    def fsync(descriptor):
        kind = 'directory' if __import__('stat').S_ISDIR(os.fstat(descriptor).st_mode) else 'receipt'
        flushes.append(kind)
        if kind == failed_flush:
            raise OSError('synthetic flush failure')
        return real_fsync(descriptor)
    def open_file(path, flags, *args):
        if failed_flush == 'directory_open' and Path(path) == out:
            raise OSError('synthetic flush failure')
        return real_open(path, flags, *args)
    monkeypatch.setattr(receipt.os, 'fsync', fsync)
    monkeypatch.setattr(receipt.os, 'open', open_file)
    with pytest.raises(OSError, match='synthetic flush failure'):
        receipt.publish(stage, out)
    assert not (out / 'exo-os-build-success.json').exists()
    assert next((out / 'build-history').glob('*/exo-os-build-success.json')).read_bytes() == prior_bytes
    accepted = accept_without_qemu(root, env)
    assert accepted.returncode != 0 and not (root / 'QEMU_WAS_STARTED').exists()
    assert flushes == (['receipt'] if failed_flush in ('receipt', 'directory_open') else ['receipt', 'directory'])


def local_git_core_fixture(tmp_path, *, worktree=False):
    root, env = mock_build_fixture(tmp_path)
    core = root / '.deps/exo'
    (core / '.EXO_PIN').unlink()
    subprocess.run(['git', 'init', '-q', str(core)], check=True)
    subprocess.run(['git', '-C', str(core), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(core), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'synthetic core'], check=True)
    pin = subprocess.check_output(['git', '-C', str(core), 'rev-parse', 'HEAD'], text=True).strip()
    if worktree:
        store = root / '.deps/core-store.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(core), str(store)], check=True)
        shutil.rmtree(core)  # Only the disposable synthetic fixture is replaced.
        subprocess.run(['git', '-C', str(store), 'worktree', 'add', '-q', '--detach', str(core), pin], check=True)
        subprocess.run(['git', '-C', str(core), 'remote', 'set-url', 'origin', str(store)], check=True)
        assert (core / '.git').is_file()
    else:
        subprocess.run(['git', '-C', str(core), 'remote', 'add', 'origin', str(core)], check=True)
    (root / 'exo.pin').write_text(pin + '\n')
    uv_log = tmp_path / 'uv-invocations'
    (tmp_path / 'bin/uv').write_text('#!/bin/sh\nprintf invoked >> "$MOCK_UV_LOG"\nexit 0\n')
    env['MOCK_UV_LOG'] = str(uv_log)
    return root, env, core, pin, uv_log


@pytest.mark.parametrize('worktree', [False, True])
@pytest.mark.parametrize('dirty', ['tracked', 'untracked'])
def test_core_git_pin_is_clean_and_dirty_core_refusal_preserves_raw(tmp_path, worktree, dirty):
    root, env, core, pin, uv_log = local_git_core_fixture(tmp_path, worktree=worktree)
    success = build(root, env, vmtest=True)
    assert success.returncode == 0, success.stderr
    assert subprocess.check_output(['git', '-C', str(core), 'rev-parse', 'HEAD'], text=True).strip() == pin
    assert not subprocess.check_output(['git', '-C', str(core), 'status', '--porcelain'], text=True).strip()
    out = root / 'image/out'
    raw_before = (out / 'exo-os.raw').read_bytes()
    manifest_before = (out / 'extra/opt/exo/EXO_OS_BUILD.json').read_bytes()
    uv_before = uv_log.read_bytes()
    changed = core / ('plugins/exo-gate/plugin.py' if dirty == 'tracked' else 'plugins/exo-gate/untracked.py')
    changed.write_text('# dirty source must not be labeled with a clean Git pin\n')
    refused = build(root, env, vmtest=True)
    assert refused.returncode == 2 and 'core source must be a clean committed checkout' in refused.stderr
    assert changed.read_text().startswith('# dirty source')
    assert uv_log.read_bytes() == uv_before
    assert (out / 'exo-os.raw').read_bytes() == raw_before
    assert (out / 'extra/opt/exo/EXO_OS_BUILD.json').read_bytes() == manifest_before
    assert not (out / 'exo-os-build-success.json').exists()
    assert list(out.glob('.build-attempt.*/prior-success.json'))
    accepted = accept_without_qemu(root, env)
    assert accepted.returncode != 0 and not (root / 'QEMU_WAS_STARTED').exists()


def test_unavailable_core_git_status_is_not_treated_as_clean(tmp_path):
    root, env, core, pin, uv_log = local_git_core_fixture(tmp_path)
    assert build(root, env, vmtest=True).returncode == 0
    raw_before = (root / 'image/out/exo-os.raw').read_bytes()
    uv_before = uv_log.read_bytes()
    wrapper = tmp_path / 'bin/git'
    wrapper.write_text('''#!/bin/sh
case "$*" in *"status --porcelain --untracked-files=all"*) exit 9;; esac
exec "$MOCK_REAL_GIT" "$@"
''')
    wrapper.chmod(0o755)
    env['MOCK_REAL_GIT'] = shutil.which('git')
    refused = build(root, env, vmtest=True)
    assert refused.returncode == 2 and 'core checkout status could not be verified' in refused.stderr
    assert uv_log.read_bytes() == uv_before
    assert (root / 'image/out/exo-os.raw').read_bytes() == raw_before
    assert not (root / 'image/out/exo-os-build-success.json').exists()


def test_core_dependency_symlink_refused_without_touching_external_source(tmp_path):
    root, env, core, pin, uv_log = local_git_core_fixture(tmp_path)
    assert build(root, env, vmtest=True).returncode == 0
    raw_before = (root / 'image/out/exo-os.raw').read_bytes()
    uv_before = uv_log.read_bytes()
    external = tmp_path / 'external-synthetic-core'
    core.rename(external)
    core.symlink_to(external, target_is_directory=True)
    plugin = external / 'plugins/exo-gate/plugin.py'
    before = plugin.read_bytes()
    refused = build(root, env, vmtest=True)
    assert refused.returncode == 2 and 'core source directory must be inside this source tree' in refused.stderr
    assert plugin.read_bytes() == before
    assert uv_log.read_bytes() == uv_before
    assert (root / 'image/out/exo-os.raw').read_bytes() == raw_before
    assert not (root / 'image/out/exo-os-build-success.json').exists()


@pytest.mark.parametrize('args', [['--tools-tree=/tmp/custom'], ['--tools-tree', '/tmp/custom'],
                                  ['--tools-tree=no']])
def test_unrecorded_tools_tree_is_refused(args):
    with pytest.raises(ValueError, match='only --tools-tree=yes'):
        module('build-profile').selected_profile(args)


def test_generated_debian_tools_tree_is_supported():
    assert module('build-profile').selected_profile([
        '--profile=vmtest', '--tools-tree=yes', '--tools-tree-distribution=debian',
        '--tools-tree-release=sid']) == 'vmtest'


@pytest.mark.parametrize('payload', ['image/mkosi.extra/opt/exo/plugins/exo-os/__pycache__/hidden.pyc',
                                     'image/mkosi.extra/opt/exo/plugins/exo-os/hidden.pyc',
                                     '.deps/exo/plugins/exo-gate/__pycache__/hidden.pyc'])
def test_ignored_payload_bytecode_refused_without_success_receipt(tmp_path, payload):
    root, env = mock_build_fixture(tmp_path)
    commit_source(root)
    assert build(root, env, vmtest=True).returncode == 0
    out = root / 'image/out'
    before = {name: (out / name).read_bytes() for name in ('exo-os.raw', 'extra/opt/exo/EXO_OS_BUILD.json')}
    target = root / payload
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'synthetic ignored bytecode')
    with (root / '.git/info/exclude').open('a') as stream:
        stream.write('\n*.pyc\n')
    assert not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip()
    refused = build(root, env, vmtest=True)
    assert refused.returncode != 0 and 'bytecode is unsupported' in refused.stderr
    assert not (out / 'exo-os-build-success.json').exists()
    assert {name: (out / name).read_bytes() for name in before} == before
    assert list(out.glob('.build-attempt.*/prior-success.json'))
    assert not list(out.glob('.build-attempt.*/exo-os.raw'))


@pytest.mark.parametrize('payload', ['src/fixture.py', 'tools/env.sh', 'image/mkosi.conf',
                                     '.deps/exo/src/runtime.py', '.deps/exo/plugins/exo-gate/plugin.py'])
def test_post_mkosi_input_drift_prevents_publication(tmp_path, payload):
    root, env = mock_build_fixture(tmp_path)
    target = root / payload
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text('# runtime fixture\n')
    assert build(root, env, vmtest=True).returncode == 0
    out = root / 'image/out'
    before = {name: (out / name).read_bytes() for name in ('exo-os.raw', 'extra/opt/exo/EXO_OS_BUILD.json')}
    mkosi = Path(env['EXO_OS_MKOSI'])
    mkosi.write_text(mkosi.read_text().replace('exit "${MOCK_MKOSI_STATUS:-0}"',
                                               'printf "\\n# changed during mkosi\\n" >> "$MOCK_DRIFT_TARGET"\nexit 0'))
    refused = build(root, {**env, 'MOCK_DRIFT_TARGET': str(target)}, vmtest=True)
    assert refused.returncode != 0 and 'build inputs changed' in refused.stderr
    assert not (out / 'exo-os-build-success.json').exists()
    assert {name: (out / name).read_bytes() for name in before} == before
    assert list(out.glob('.build-attempt.*/prior-success.json'))
    assert list(out.glob('.build-attempt.*/exo-os.raw'))
    assert accept_without_qemu(root, env).returncode != 0
    assert not (root / 'QEMU_WAS_STARTED').exists()


def test_ignored_unchecked_helper_bytecode_cannot_replace_source_identity(tmp_path):
    root = source_fixture(tmp_path)
    replacement = tmp_path / 'replacement.py'
    replacement.write_text('def source_identity(*args, **kwargs): return "c" * 40\n')
    cache = Path(importlib.util.cache_from_source(str(root / 'tools/source_identity.py')))
    cache.parent.mkdir()
    py_compile.compile(str(replacement), cfile=str(cache),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    run = subprocess.run([sys.executable, str(root / 'tools/build-provenance.py'),
                          '--source-root', str(root), '--pin', 'b' * 40,
                          '--output', str(root / 'manifest.json')], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads((root / 'manifest.json').read_text())['exo_os_commit'] == 'a' * 40

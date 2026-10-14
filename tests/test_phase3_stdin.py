"""Actual process/pipe tests using a wtype stub, with no GTK/native claims."""
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from phase3_stdin import finish_keys_with_input, ready_for_y, start_keys


ARGV = ['-P', 'F6', '-', '-s', '800', '-d', '150', 'yesterday', '-p', 'F6']


@pytest.fixture
def stub(tmp_path, monkeypatch):
    path = tmp_path / 'wtype'
    path.write_text('#!' + sys.executable + '''
import json, os, sys, time
from pathlib import Path
log = Path(os.environ['STUB_EVENT_LOG'])
def record(stage, **fields):
 with log.open('a') as output: output.write(json.dumps({'stage': stage, 'pid': os.getpid(), **fields})+'\\n')
assert sys.argv[1:] == ['-P','F6','-','-s','800','-d','150','yesterday','-p','F6']
# Simulated key map is built from the complete argv before the held stage.
record('argv-keymap-built', y_code=2)
record('F6-held')
record('stdin-blocked')
text = sys.stdin.read()  # one character does not advance until EOF
record('stdin-eof', text=text)
record('same-keyboard-keymap-upload', y_code=2)
record('y-typed', text=text)
if os.environ.get('STUB_HANG') == '1': time.sleep(30)
time.sleep(.12)  # force timeout retries for a real communicate() operation
for char in 'yesterday': record('text-character', char=char, f6_held=True)
record('F6-released')
sys.stdout.write('stdout fixture\\n')
sys.stderr.write('stderr fixture\\n')
raise SystemExit(int(os.environ.get('STUB_EXIT','0')))
''')
    path.chmod(0o755)
    log = tmp_path / 'events.jsonl'
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('STUB_EVENT_LOG', str(log))
    return log


def events(path):
    import json
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def await_blocked(proc, log):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(row['stage'] == 'stdin-blocked' for row in events(log)):
            return
        assert proc.poll() is None
        time.sleep(.01)
    raise AssertionError('stub did not reach stdin')


def test_no_y_until_review_active_arm_and_single_input_on_timeout_retry(stub):
    ui = SimpleNamespace(model=SimpleNamespace(reviewed_approval=None),
                         card=SimpleNamespace(is_active=lambda: False, armed=lambda: False))
    proc = start_keys(*ARGV, stdin=subprocess.PIPE)
    try:
        await_blocked(proc, stub)
        assert not ready_for_y(ui)
        ui.model.reviewed_approval = SimpleNamespace(request_id='held_f6_a')
        assert not ready_for_y(ui)
        ui.card.is_active = lambda: True
        assert not ready_for_y(ui)
        # The stub remains blocked with no Y before the controlled ready flag.
        assert not any(row['stage'] == 'stdin-eof' for row in events(stub))
        ui.card.armed = lambda: True
        assert ready_for_y(ui)
        calls = []
        original_communicate = proc.communicate

        def observed(*args, **kwargs):
            calls.append(kwargs.get('input'))
            assert ready_for_y(ui)
            return original_communicate(*args, **kwargs)

        proc.communicate = observed
        pumps = []
        result = finish_keys_with_input(proc, 'y', lambda: pumps.append(True), lambda label: pytest.fail(label))
        assert result == (None, 'stderr fixture\n')  # existing stdout=DEVNULL contract
        assert calls[0] == 'y' and len(calls) > 1 and all(value is None for value in calls[1:])
        assert len(pumps) == len(calls)
        recorded = events(stub)
        assert len({row['pid'] for row in recorded}) == 1
        assert [row['text'] for row in recorded if row['stage'] == 'stdin-eof'] == ['y']
        assert ''.join(row['char'] for row in recorded if row['stage'] == 'text-character') == 'yesterday'
        assert recorded[-1]['stage'] == 'F6-released'
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=2)


def test_one_y_without_eof_remains_buffered(stub):
    proc = start_keys(*ARGV, stdin=subprocess.PIPE)
    try:
        await_blocked(proc, stub)
        proc.stdin.write('y')
        proc.stdin.flush()
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=.05)
        assert not any(row['stage'] == 'stdin-eof' for row in events(stub))
        # This direct-close case only tests EOF semantics. Production uses the
        # first communicate(input='y') to write and close the pipe atomically.
        proc.stdin.close()
        proc.stdin = None
        assert proc.communicate(timeout=3) == (None, 'stderr fixture\n')
        assert [row['text'] for row in events(stub) if row['stage'] == 'stdin-eof'] == ['y']
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=2)


def test_nonzero_exit_is_failure_and_stderr_is_preserved(stub, monkeypatch):
    monkeypatch.setenv('STUB_EXIT', '7')
    proc = start_keys(*ARGV, stdin=subprocess.PIPE)
    await_blocked(proc, stub)
    with pytest.raises(AssertionError, match='stderr fixture'):
        finish_keys_with_input(proc, 'y', lambda: None, lambda label: pytest.fail(label))
    assert proc.returncode == 7


def test_delivery_deadline_kills_owned_child_and_records_timeout(stub, monkeypatch):
    monkeypatch.setenv('STUB_HANG', '1')
    proc = start_keys(*ARGV, stdin=subprocess.PIPE)
    await_blocked(proc, stub)
    snapshots = []
    began = time.monotonic()
    with pytest.raises(AssertionError, match='timed out waiting'):
        finish_keys_with_input(proc, 'y', lambda: None, snapshots.append, seconds=.15)
    assert proc.poll() is not None and time.monotonic() - began < 2
    assert snapshots == ['bounded wtype stdin delivery']
    assert [row['text'] for row in events(stub) if row['stage'] == 'stdin-eof'] == ['y']


def test_exact_request_is_required_before_y():
    ui = SimpleNamespace(model=SimpleNamespace(reviewed_approval=SimpleNamespace(request_id='held_f6_b')),
                         card=SimpleNamespace(is_active=lambda: True, armed=lambda: True))
    assert not ready_for_y(ui)


def test_handshake_retries_work_with_reviewed_observer_stderr_drainer(stub, monkeypatch):
    import ast
    import threading
    source = Path(__file__).with_name('fixtures').joinpath('reviewed_observer_popen.txt').read_text()
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ObservedPopen')
    recorded = []
    namespace = {'original_popen': subprocess.Popen, 'subprocess': subprocess, 'os': os, 'threading': threading,
                 'observer': SimpleNamespace(record=lambda kind, **fields: recorded.append((kind, fields)))}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'reviewed-observer-stdin-fixture', 'exec'), namespace)
    monkeypatch.setattr(subprocess, 'Popen', namespace['ObservedPopen'])
    proc = start_keys(*ARGV, stdin=subprocess.PIPE)
    await_blocked(proc, stub)
    assert finish_keys_with_input(proc, 'y', lambda: None, lambda label: pytest.fail(label)) == (None, 'stderr fixture\n')
    assert proc.returncode == 0
    assert [row['text'] for row in events(stub) if row['stage'] == 'stdin-eof'] == ['y']
    assert any(kind == 'wtype-stderr-drained' for kind, _ in recorded)
    assert not any(kind in ('wtype-stderr-overflow', 'wtype-stderr-drain-error', 'wtype-stderr-incomplete')
                   for kind, _ in recorded)

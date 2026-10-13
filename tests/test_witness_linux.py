"""Opt-in real kernel/UID checks for the future witness image, never simulated on the host.

Run as root in the isolated Linux acceptance environment with EXO_WITNESS_LINUX_ACCEPTANCE=1.
The static image accounts, /opt/exo Python and libseccomp must already be installed.
"""
from __future__ import annotations

import ctypes
import grp
import os
import json
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from exo_os.witness.process import PYTHON, identities

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0 or os.environ.get("EXO_WITNESS_LINUX_ACCEPTANCE") != "1",
    reason="requires explicitly enabled disposable Linux root acceptance with static witness accounts")


@pytest.mark.parametrize("mode", ["export", "network"])
def test_real_uid_split_denies_export_network_and_publisher_signer_access(mode):
    signer, publisher = identities()
    root = Path(tempfile.mkdtemp(prefix="exo-witness-linux-", dir="/tmp"))
    root.chmod(0o755)
    private = root / "signer"
    private.mkdir(mode=0o700)
    os.chown(private, *signer)
    key = private / "synthetic.key"
    key.write_text("throwaway test material")
    key.chmod(0o600)
    os.chown(key, *signer)
    request = root / "request.json"
    request.write_text("{}")
    request.chmod(0o644)
    address = root / "authority.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(address))
    listener.listen(1)
    os.chown(address, 0, grp.getgrnam("exo-sys").gr_gid)
    address.chmod(0o660)
    script = '''
import os, socket, sys
from exo_os.witness import worker
mode, key, address, request = sys.argv[1:]
if mode == "export":
    # Exercise the actual entrypoint/restrict ordering; only the key-bearing export
    # callback is substituted so no installed authority's private material is read.
    def synthetic_export(request, output):
        assert os.geteuid() != 0 and os.getgroups() == []
        try:
            socket.socket(socket.AF_INET)
        except PermissionError:
            pass
        else:
            raise AssertionError("signer exporter can create a network socket before key load")
        assert open(key).read() == "throwaway test material"
    worker.export = synthetic_export
    fd = os.open(request, os.O_RDONLY)
    sys.argv = ["worker", mode, str(fd), str(os.getppid())]
    assert worker.main() == 0
else:
    worker.restrict(mode)
    assert os.geteuid() != 0 and os.getgroups() == []
    try:
        open(key)
    except PermissionError:
        pass
    else:
        raise AssertionError("publisher can read signer key")
    with socket.socket(socket.AF_UNIX) as peer:
        try:
            peer.connect(address)
        except PermissionError:
            pass
        else:
            raise AssertionError("publisher can reach authority socket")
'''
    try:
        uid, gid = signer if mode == "export" else publisher
        proc = subprocess.run([PYTHON, "-I", "-c", script, mode, str(key), str(address), str(request)],
                              user=uid, group=gid, extra_groups=[], capture_output=True,
                              text=True, check=False, timeout=10, cwd="/", env={"PATH": "/usr/bin:/bin"})
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        listener.close()
        shutil.rmtree(root)


def test_installed_separate_session_worker_dies_with_exact_parent():
    """Verify installed restrict, real static publisher UID and Linux parent-death SIGKILL."""
    _, publisher = identities()
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0  # GET_CHILD_SUBREAPER
    assert libc.prctl(36, 1, 0, 0, 0) == 0  # adopt/reap the orphaned test worker
    worker_script = r"""
import ctypes,json,os,signal,sys
from pathlib import Path
from exo_os.witness import worker
assert Path(worker.__file__).resolve().is_relative_to(Path('/opt/exo').resolve())
worker.restrict('network', int(sys.argv[1]))
value = ctypes.c_int()
assert ctypes.CDLL(None).prctl(2, ctypes.byref(value), 0, 0, 0) == 0 and value.value == signal.SIGKILL
print(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'pgid':os.getpgrp(),'uid':os.geteuid(),'pdeath':value.value}), flush=True)
signal.pause()
"""
    parent_script = r"""
import os,subprocess,sys,time
child = subprocess.Popen([sys.executable, '-I', '-B', '-c', sys.argv[1], str(os.getpid())],
                 user=int(sys.argv[2]), group=int(sys.argv[3]), extra_groups=[], stdout=subprocess.PIPE,
                 stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True, env={'PATH':'/usr/bin:/bin'}, cwd='/')
print(child.pid, flush=True)
line = child.stdout.readline(4097)
assert line.endswith(b'\n') and len(line) <= 4096
sys.stdout.buffer.write(line)
sys.stdout.buffer.flush()
time.sleep(30)
"""
    parent = None
    worker_pid = None
    reaped = False
    try:
        parent = subprocess.Popen([PYTHON, "-I", "-B", "-c", parent_script, worker_script, *map(str, publisher)],
                                  start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd="/", env={"PATH": "/usr/bin:/bin"})
        data = bytearray()
        deadline = time.monotonic() + 10
        with selectors.DefaultSelector() as selector:
            selector.register(parent.stdout, selectors.EVENT_READ)
            while data.count(b"\n") < 2:
                remaining = deadline - time.monotonic()
                assert remaining > 0 and selector.select(remaining), "installed worker readiness deadline exceeded"
                part = os.read(parent.stdout.fileno(), 4096)
                assert part and len(data) + len(part) <= 4096, "invalid or oversized worker readiness"
                data.extend(part)
                if worker_pid is None and b"\n" in data:
                    worker_pid = int(data.partition(b"\n")[0])
        ready = json.loads(data.splitlines()[1])
        assert ready == {"pid": worker_pid, "parent": parent.pid, "pgid": worker_pid,
                         "uid": publisher[0], "pdeath": signal.SIGKILL}
        assert worker_pid != parent.pid and os.getpgid(parent.pid) == parent.pid
        os.kill(parent.pid, signal.SIGKILL)  # exact parent only, never worker/group signal
        assert parent.wait(timeout=2) == -signal.SIGKILL
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid, status = os.waitpid(worker_pid, os.WNOHANG)
            if pid:
                reaped = True
                assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
                break
            time.sleep(.01)
        assert reaped, "separate-session installed worker survived parent termination"
    finally:
        if parent is not None:
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            parent.wait(timeout=2)
            parent.stdout.close()
            parent.stderr.close()
        if worker_pid is not None and not reaped:
            try:
                os.killpg(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    pid, _ = os.waitpid(worker_pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid:
                    break
                time.sleep(.01)
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0

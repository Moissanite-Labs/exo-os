"""Opt-in real kernel/UID checks for the future witness image, never simulated on the host.

Run as root in the isolated Linux acceptance environment with EXO_WITNESS_LINUX_ACCEPTANCE=1.
The static image accounts, /opt/exo Python and libseccomp must already be installed.
"""
from __future__ import annotations

import grp
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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

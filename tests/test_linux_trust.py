"""Actual kernel-peer and default trust-path checks, only in our disposable root container."""
import json
import os
import pwd
import socket
import subprocess
import sys
import threading

import pytest

from exo_os.sysd.protocol import PUBLIC_KEY
from exo_os.sysd.server import Server

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0 or os.environ.get("EXO_OS_TEST_CONTAINER") != "1",
    reason="requires tools/test-linux.sh disposable root container",
)


def test_unprivileged_client_authenticates_root_using_kernel_and_default_key(socket_dir):
    # Exercise the real production client defaults in another UID, with no injected peer/key.
    class ReadOnlyActions:
        def table(self):
            return {"status": lambda args: {"transport_verified": True}}

    socket_dir.chmod(0o755)
    path = socket_dir / "root.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(0o666)
    listener.listen(1)
    server = Server(ReadOnlyActions(), key_path=socket_dir / "keys" / "signing.key",
                    log_path=socket_dir / "root.log")

    def serve_once():
        with listener:
            conn, _ = listener.accept()
            server._client(conn)

    worker = threading.Thread(target=serve_once, daemon=True)
    worker.start()
    nobody = pwd.getpwnam("nobody")
    code = "from exo_os.sysd.client import SysdClient; import json,sys; print(json.dumps(SysdClient(sys.argv[1]).call('status')))"
    try:
        result = subprocess.run([sys.executable, "-c", code, str(path)], user=nobody.pw_uid,
                                group=nobody.pw_gid, extra_groups=[], capture_output=True, text=True, timeout=10)
        worker.join(2)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"transport_verified": True}
        receipt = json.loads((socket_dir / "root.log").read_text())
        assert receipt["uid"] == nobody.pw_uid
        assert receipt["pid"] != os.getpid()
        assert PUBLIC_KEY.stat().st_uid == 0
        assert PUBLIC_KEY.stat().st_mode & 0o777 == 0o644
    finally:
        listener.close()
        PUBLIC_KEY.unlink(missing_ok=True)

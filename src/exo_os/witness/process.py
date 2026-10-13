"""Bounded exec-only UID separation. The root parent never opens a network connection."""
from __future__ import annotations

import contextlib
import os
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .state import CREDENTIALS, WitnessError, directory, encoded, read_file

MAX_EXPORT = 64 * 1024 * 1024
CHILD_SECONDS = 90
PYTHON = "/opt/exo/bin/python"


def account(name):
    with directory(Path("/etc")) as fd:
        lines = read_file(fd, "passwd", mode=0o644).decode().splitlines()
    rows = [line.split(":") for line in lines if line.split(":", 1)[0] == name]
    if len(rows) != 1 or len(rows[0]) != 7 or not all(v.isdigit() for v in rows[0][2:4]):
        raise WitnessError("required static witness/signer account is unavailable")
    uid, gid = map(int, rows[0][2:4])
    if uid == 0 or gid == 0:
        raise WitnessError("witness helpers cannot use root identity or group")
    return uid, gid


def identities():
    signer, publisher = account("exo-ledger"), account("exo-witness")
    desktop = account("exo")
    if publisher[0] in (signer[0], desktop[0]) or publisher[1] in (signer[1], desktop[1]):
        raise WitnessError("witness publisher must have a separate static identity")
    with directory(Path("/etc")) as fd:
        groups = [line.split(":") for line in read_file(fd, "group", mode=0o644).decode().splitlines()]
    own = [row for row in groups if len(row) == 4 and row[0] == "exo-witness"]
    if (len(own) != 1 or own[0][2] != str(publisher[1]) or own[0][3] or
            any(len(row) == 4 and row[0] != "exo-witness" and row[2] == str(publisher[1]) for row in groups)):
        raise WitnessError("witness publisher group must be separate and have no supplemental members")
    return signer, publisher


def collect(proc, limit, timeout=CHILD_SECONDS):
    """Drain both pipes with fixed byte/deadline bounds; kill/reap on every failure."""
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream, destination, bound in ((proc.stdout, output, limit), (proc.stderr, errors, 8192)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, (destination, bound))
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WitnessError("witness helper deadline exceeded; delivery outcome may be unknown", "unavailable")
                for key, _ in selector.select(min(remaining, 0.1)):
                    part = os.read(key.fd, 16384)
                    if not part:
                        selector.unregister(key.fileobj)
                        continue
                    destination, bound = key.data
                    if len(destination) + len(part) > bound:
                        raise WitnessError("witness helper output exceeded its bound", "unavailable")
                    destination.extend(part)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or proc.wait(timeout=remaining):
                # Never echo worker stderr: it may contain credential/file details.
                raise WitnessError("witness helper refused or failed; delivery outcome may be unknown", "unavailable")
        return bytes(output)
    except subprocess.TimeoutExpired as exc:
        raise WitnessError("witness helper deadline exceeded; delivery outcome may be unknown", "unavailable") from exc
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=2)
        proc.stdout.close()
        proc.stderr.close()


def execute(mode, request, identity, *, limit):
    from ..sandbox.policy import trusted_path
    trusted_path(Path(PYTHON).resolve(strict=True))
    uid, gid = identity
    with tempfile.TemporaryFile() as input_file:
        raw = encoded(request)
        if len(raw) > 131072:
            raise WitnessError("witness helper input exceeds limit")
        input_file.write(raw)
        input_file.seek(0)
        # exec clears parent memory; in particular a publisher never inherits loaded authority keys.
        proc = subprocess.Popen([PYTHON, "-I", "-m", "exo_os.witness.worker", mode,
                                 str(input_file.fileno()), str(os.getpid())],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                pass_fds=(input_file.fileno(),), close_fds=True, start_new_session=True,
                                user=uid, group=gid, extra_groups=[], umask=0o077,
                                cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"})
        return collect(proc, limit)


@contextlib.contextmanager
def snapshots(credentials, spool, gid, *, uid=0):
    """Root-owned, group-readable immutable snapshots, removed even after child failure."""
    with directory(Path("/run")), tempfile.TemporaryDirectory(prefix="exo-witness-", dir="/run") as path:
        os.chown(path, 0, gid)
        os.chmod(path, 0o750)
        scratch = Path(path) / "scratch"
        scratch.mkdir(mode=0o700)
        os.chown(scratch, uid, gid)
        for name, raw in {**credentials, "checkpoints.jsonl": spool}.items():
            if name not in (*CREDENTIALS, "checkpoints.jsonl"):
                raise WitnessError("unexpected witness snapshot file")
            fd = os.open(Path(path) / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fchown(output.fileno(), 0, gid)
                os.fchmod(output.fileno(), 0o440)
        yield path

class Workers:
    def export(self, policy):
        signer, _ = identities()
        return execute("export", {"policy": policy}, signer, limit=MAX_EXPORT + 8193)

    def network(self, policy, credentials, spool, *, publish, nonce):
        _, publisher = identities()
        with snapshots(credentials, spool, publisher[1], uid=publisher[0]) as path:
            return execute("network", {"policy": policy, "path": path, "publish": publish, "nonce": nonce},
                           publisher, limit=16384)

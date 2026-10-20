"""Socket-activated root helper: bounded connections and signed, request-bound receipts."""
from __future__ import annotations

import argparse
import os
import re
import socket
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .actions import ActionError, Actions
from .protocol import MAX_REPLY, MAX_REQUEST, PUBLIC_KEY, READ_ONLY, VERSION, decode, encode, peer_credentials, read_line

KEY = Path('/var/lib/exo-sysd/sysd.key')
LOG = Path('/var/lib/exo-sysd/audit.log')
MAX_CONNECTIONS = 8
IO_TIMEOUT = 5.0


class Server:
    def __init__(self, actions: Actions, key_path: Path = KEY, log_path: Path | None = LOG,
                 allowed_gid: int | None = None, *, public_key_path: Path = PUBLIC_KEY,
                 max_connections: int = MAX_CONNECTIONS, io_timeout: float = IO_TIMEOUT) -> None:
        self.actions, self.table = actions, actions.table()
        self.lock = threading.Lock()  # audit-log open, append and close only; never held while an action runs
        self.mutation_lock = threading.Lock()  # mutating actions run one at a time
        self.slots = threading.BoundedSemaphore(max_connections)
        self.io_timeout = io_timeout
        self.log_path, self.allowed_gid = log_path, allowed_gid
        self.key = _load_or_make_key(key_path)
        _publish_public_key(public_key_path, self.key)

    def handle_line(self, line: bytes, peer: tuple[int, int, int]) -> dict[str, Any]:
        pid, uid, _ = peer
        try:
            if len(line) > MAX_REQUEST:
                raise ValueError
            req = decode(line)
            if (not isinstance(req, dict) or set(req) != {'version', 'request_id', 'action', 'args'}
                    or type(req['version']) is not int or req['version'] != VERSION
                    or not isinstance(req['request_id'], str)
                    or not re.fullmatch(r'[0-9a-f]{64}', req['request_id'])
                    or not isinstance(req['action'], str) or not isinstance(req['args'], dict)):
                raise ValueError
            action, args = req['action'], req['args']
        except (ValueError, KeyError, TypeError, RecursionError):
            return {'ok': False, 'error': 'bad request'}
        fn = self.table.get(action)
        if fn is None:
            return {'ok': False, 'error': f'exo-sysd does not do {action!r}'}
        started = time.monotonic()
        # Mutations run one at a time, in the order they take the mutation lock. The audit log is opened
        # first: a known audit outage must never cause a mutation to run. The action itself runs outside
        # the log lock, so a slow action cannot block other requests.
        mutation = None if action in READ_ONLY else self.mutation_lock
        if mutation is not None:
            mutation.acquire()
        try:
            log = None
            try:
                with self.lock:
                    log = self._open_log()
            except OSError:
                result, ok, err = None, False, 'root audit log unavailable; action was not run'
                execution = 'not_started'
            else:
                try:
                    result, ok, err = fn(args), True, None
                    execution = 'completed'
                except ActionError as e:
                    result, ok, err = None, False, str(e)
                    execution = 'failed_or_partial'
                except Exception as e:
                    result, ok, err = None, False, f'internal error: {type(e).__name__}: {e}'
                    execution = 'failed_or_partial'
            receipt = self._receipt(req, ok, result, err, uid, pid, started, execution)
            try:
                if log is not None:
                    with self.lock:
                        try:
                            log.write(encode(receipt))
                            log.flush()
                            os.fsync(log.fileno())
                        finally:
                            try:
                                log.close()  # inside the lock: a failed flush must not retry unlocked
                            except OSError:
                                pass
            except OSError:
                # Preserve success and the actual result. Sign the audit failure too.
                receipt = self._receipt(req, ok, result, err, uid, pid, started, execution,
                                        'root audit log append failed')
        finally:
            if mutation is not None:
                mutation.release()
        return {key: receipt[key] for key in ('ok', 'result', 'error', 'audit_error', 'execution')} | {'receipt': receipt}

    def _open_log(self):
        if self.log_path is None:  # explicit test-only dependency injection
            return None
        fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            meta = os.fstat(fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o022:
                raise OSError('unsafe root audit log')
            return os.fdopen(fd, 'ab')
        except Exception:
            os.close(fd)
            raise

    def _receipt(self, req: dict, ok: bool, result: Any, err: str | None, uid: int, pid: int,
                 started: float, execution: str, audit_error: str | None = None) -> dict[str, Any]:
        from exo import crypto

        doc = {**req, 'by': 'exo-sysd', 'ok': ok, 'result': result, 'error': err, 'audit_error': audit_error,
               'execution': execution, 'uid': uid, 'pid': pid, 'ts': crypto.now_ts(), 'ms': int((time.monotonic() - started) * 1000),
               'pub': crypto.pubkey_b64(self.key)}
        return crypto.sign_doc(doc, self.key)

    def serve(self, sock: socket.socket) -> None:
        while True:
            conn, _ = sock.accept()
            if not self.slots.acquire(blocking=False):
                conn.close()
                continue
            try:
                threading.Thread(target=self._client_slot, args=(conn,), daemon=True).start()
            except Exception:
                conn.close()
                self.slots.release()
                raise

    def _client_slot(self, conn: socket.socket) -> None:
        try:
            self._client(conn)
        finally:
            self.slots.release()

    def _client(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(self.io_timeout)
                peer = peer_credentials(conn)
                if not self._allowed(peer[1], peer[2]):
                    return
                line = read_line(conn, MAX_REQUEST, self.io_timeout)
                reply = encode(self.handle_line(line, peer))
                if len(reply) > MAX_REPLY:
                    return  # client reports an unknown outcome; never truncates a signed result
                conn.settimeout(self.io_timeout)
                conn.sendall(reply)
            except (OSError, ValueError, TypeError, RecursionError):
                return

    def _allowed(self, uid: int, gid: int) -> bool:
        if uid == 0 or self.allowed_gid is None:
            return True
        import pwd

        try:
            groups = os.getgrouplist(pwd.getpwuid(uid).pw_name, gid)
        except KeyError:
            return False
        return self.allowed_gid in groups


def _load_or_make_key(path: Path):
    from exo import crypto

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise PermissionError('signing-key directory is not private to the helper')
    if not path.exists():
        crypto.save_key(crypto.keygen(), path)
    meta = path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
        raise PermissionError('signing key must be a helper-owned 0600 regular file')
    return crypto.load_key(path)


def _publish_public_key(path: Path, key) -> None:
    from exo import crypto

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    meta = path.parent.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o022:
        raise PermissionError('public-key directory must belong to the helper')
    fd, temporary = tempfile.mkstemp(prefix='.receipt-pub-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o644)
            f.write((crypto.pubkey_b64(key) + '\n').encode())
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='exo-sysd')
    ap.add_argument('--socket', default='/run/exo-sysd.sock', help='used only without systemd socket activation')
    ap.add_argument('--group', default='exo-sys', help='only members of this group may connect')
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print('exo-sysd must run as root (it is the only part of Exo OS that does)', file=sys.stderr)
        return 1
    import grp

    try:
        gid = grp.getgrnam(a.group).gr_gid
    except KeyError:
        print(f'group {a.group} does not exist', file=sys.stderr)
        return 1
    if os.environ.get('LISTEN_FDS') == '1' and os.environ.get('LISTEN_PID') == str(os.getpid()):
        sock = socket.socket(fileno=3)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(a.socket):
            os.unlink(a.socket)
        sock.bind(a.socket)
        os.chown(a.socket, 0, gid)
        os.chmod(a.socket, 0o660)
        sock.listen(MAX_CONNECTIONS)
    Server(Actions(), allowed_gid=gid).serve(sock)
    return 0


if __name__ == '__main__':
    sys.exit(main())

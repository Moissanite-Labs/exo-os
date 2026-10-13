"""Authenticate the root peer, verify a request-bound receipt, then record it."""
from __future__ import annotations

import fcntl
import os
import re
import secrets
import socket
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import paths
from ..ledger import open_sink
from .protocol import MAX_REPLY, MAX_REQUEST, MUTATIONS, NAMED_ACTIONS, PUBLIC_KEY, VERSION
from .protocol import decode, encode, peer_credentials, read_line


class SysdError(Exception):
    """Refusal or an untrusted/unknown outcome. Never automatically retry mutations."""
    retryable = False


class CompletedButUnrecorded(SysdError):
    """The verified action completed. Recover the receipt; never rerun the action."""
    completed = True

    def __init__(self, result: Any, receipt: dict, outbox_path: Path | None, reason: str) -> None:
        self.result, self.receipt, self.outbox_path = result, receipt, outbox_path
        self.reason = reason
        recovery = f' Receipt saved at {outbox_path}.' if outbox_path else ' Receipt is attached to this outcome.'
        super().__init__(f'Action completed; receipt not recorded: {reason}. Do not retry the action.{recovery}')

    def to_dict(self) -> dict:
        return {'completed': True, 'retryable': False, 'result': self.result, 'receipt': self.receipt,
                'outbox_path': str(self.outbox_path) if self.outbox_path else None,
                'audit_error': self.reason, 'message': str(self), 'execution': self.receipt['execution']}


def _trust_key(path: Path) -> str:
    """No user-writable ancestor, symlink or substituted key is a trust anchor."""
    from exo import crypto

    path = path.absolute()
    for ancestor in reversed(path.parents):
        meta = ancestor.lstat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise ValueError(f'untrusted key directory: {ancestor}')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        meta = os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise ValueError('receipt key must be a root-owned, non-writable regular file')
        public = f.read(129).decode('ascii').strip()
    if len(crypto.unb64(public)) != 32:
        raise ValueError('invalid receipt public key')
    return public


def _outbox_dir() -> Path:
    directory = paths.exo_home() / 'sysd-outbox'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.geteuid():
        raise OSError('receipt outbox must belong to the caller')
    os.chmod(directory, 0o700)
    return directory


def _save_receipt(directory: Path, receipt: dict) -> Path:
    destination = directory / (receipt['request_id'] + '.json')
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(encode(receipt))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


@contextmanager
def _outbox_lock(directory: Path):
    fd = os.open(directory / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
            raise OSError('unsafe receipt outbox lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _valid_receipt(receipt, public: str) -> bool:
    from exo import crypto

    fields = {'version', 'request_id', 'action', 'args', 'by', 'ok', 'result', 'error', 'audit_error',
              'execution', 'uid', 'pid', 'ts', 'ms', 'pub', 'sig'}
    try:
        return (isinstance(receipt, dict) and set(receipt) == fields
                and type(receipt['version']) is int and receipt['version'] == VERSION
                and receipt['by'] == 'exo-sysd' and receipt['pub'] == public
                and isinstance(receipt['request_id'], str)
                and re.fullmatch(r'[0-9a-f]{64}', receipt['request_id']) is not None
                and isinstance(receipt['action'], str) and receipt['action'] in NAMED_ACTIONS
                and isinstance(receipt['args'], dict) and type(receipt['ok']) is bool
                and type(receipt['uid']) is int and receipt['uid'] >= 0
                and type(receipt['pid']) is int and receipt['pid'] > 0
                and type(receipt['ms']) is int and receipt['ms'] >= 0
                and isinstance(receipt['ts'], str) and len(receipt['ts']) <= 128
                and (receipt['audit_error'] is None or isinstance(receipt['audit_error'], str))
                and ((receipt['ok'] and receipt['execution'] == 'completed' and receipt['error'] is None)
                     or (not receipt['ok'] and receipt['execution'] in {'not_started', 'failed_or_partial'}
                         and isinstance(receipt['error'], str)))
                and crypto.verify_doc(receipt, public))
    except (ValueError, TypeError, RecursionError):
        return False


def _recorded_receipts(sink, public: str) -> dict[str, dict]:
    """Dedupe only a matching, root-verified receipt in a real system.action row."""
    if hasattr(sink, 'ledger'):
        events = (event.get('body', {}) for event in sink.ledger.iter(kind='action'))
    elif hasattr(sink, 'events'):  # explicit MemorySink test injection
        events = iter(sink.events)
    else:
        raise OSError('no principal ledger is available')
    recorded = {}
    for body in events:
        if not isinstance(body, dict):
            continue
        receipt = body.get('receipt')
        if body.get('phase') == 'system.action' and _valid_receipt(receipt, public):
            recorded[receipt['request_id']] = receipt
    return recorded


class SysdClient:
    def __init__(self, path: str | None = None, timeout: float = 1800, *,
                 expected_peer_uid: int = 0, trust_key: str | None = None) -> None:
        # Explicit dependency injection is for tests/embedding; there is no environment bypass.
        self.path = path or os.environ.get('EXO_SYSD_SOCKET', '/run/exo-sysd.sock')
        self.timeout, self.expected_peer_uid, self.trust_key = timeout, expected_peer_uid, trust_key

    def call(self, action: str, args: dict[str, Any] | None = None, *, record: bool = True) -> Any:
        from exo import crypto

        if not isinstance(action, str) or action not in NAMED_ACTIONS:
            raise SysdError(f'exo-sysd does not do {action!r}')
        if args is not None and not isinstance(args, dict):
            raise SysdError('action args must be an object')
        if not record and action in MUTATIONS:
            raise SysdError('mutating actions require receipt recording')
        request = {'version': VERSION, 'request_id': secrets.token_hex(32), 'action': action,
                   'args': {} if args is None else args}
        try:
            wire = encode(request)
            if len(wire) > MAX_REQUEST:
                raise ValueError('request too large')
            # Freeze arguments through the wire representation before sending/verification.
            request = decode(wire)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.path)
                _, uid, _ = peer_credentials(s)
                if uid != self.expected_peer_uid:
                    raise ValueError(f'system helper peer uid {uid} is not trusted')
                directory = _outbox_dir() if record and action in MUTATIONS else None
                s.sendall(wire)
                reply = decode(read_line(s, MAX_REPLY, self.timeout))
                # Socket activation may complete connect before the daemon publishes its key.
                # Root peer authentication already protects the request transport.
                public = self.trust_key if self.trust_key is not None else _trust_key(PUBLIC_KEY)
        except (OSError, ValueError, TypeError, RecursionError) as e:
            raise SysdError(f'The system helper is not reachable or its response is untrusted ({e}). '
                            'If a request was sent, its outcome is unknown; do not retry a mutation.') from None
        if not isinstance(reply, dict) or type(reply.get('ok')) is not bool:
            raise SysdError('Untrusted system helper response: invalid reply; outcome unknown, do not retry.')
        receipt = reply.get('receipt')
        expected = {key: request[key] for key in ('version', 'request_id', 'action', 'args')}
        expected.update(by='exo-sysd', uid=os.geteuid(), pid=os.getpid())
        expected.update({key: reply.get(key) for key in ('ok', 'result', 'error', 'audit_error', 'execution')})
        try:
            valid = (_valid_receipt(receipt, public)
                     and all(crypto.canonical(receipt.get(key)) == crypto.canonical(value)
                             for key, value in expected.items()))
        except (ValueError, TypeError, RecursionError):
            valid = False
        if not valid:
            raise SysdError('Untrusted system helper receipt: missing, forged or mismatched; '
                            'outcome unknown, do not retry the action.')
        outbox_path = None
        if directory is not None:
            try:
                with _outbox_lock(directory):
                    outbox_path = _save_receipt(directory, receipt)
                    event_id = open_sink(paths.store(), paths.exo_home()).append('system.action', {'receipt': receipt})
                    if event_id is None:
                        raise OSError('no principal ledger is available')
            except Exception as e:
                if reply['ok']:
                    raise CompletedButUnrecorded(reply.get('result'), receipt, outbox_path, str(e)) from None
                raise SysdError(f"Action refused or failed: {reply.get('error')}. Receipt recording failed: {e}; "
                                'do not retry without checking system state.') from None
        if not reply['ok']:
            raise SysdError((reply.get('error') or 'the system helper refused')
                            + '; execution=' + receipt['execution']
                            + '. Do not retry without checking system state.')
        if reply.get('audit_error'):
            raise CompletedButUnrecorded(reply.get('result'), receipt, outbox_path, reply['audit_error'])
        return reply.get('result')

    def recover_pending(self) -> dict:
        """Record authentic saved receipts only. Never connect to the helper or repeat actions."""
        from exo import crypto

        summary = {'recorded': [], 'already_recorded': [], 'rejected': [], 'errors': [], 'root_audit_errors': []}
        try:
            public = self.trust_key if self.trust_key is not None else _trust_key(PUBLIC_KEY)
            directory = _outbox_dir()
            with _outbox_lock(directory):
                sink = open_sink(paths.store(), paths.exo_home())
                recorded = _recorded_receipts(sink, public)
                for path in sorted(directory.glob('*.json')):
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                        with os.fdopen(fd, 'rb') as f:
                            meta = os.fstat(f.fileno())
                            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
                                raise ValueError('outbox receipt is not a caller-owned private regular file')
                            wire = f.read(MAX_REPLY + 1)
                        if len(wire) > MAX_REPLY:
                            raise ValueError('oversize receipt')
                        receipt = decode(wire)
                        if (not _valid_receipt(receipt, public) or receipt['uid'] != os.geteuid()
                                or receipt['request_id'] != path.stem or receipt['action'] not in MUTATIONS):
                            raise ValueError('untrusted or mismatched saved receipt')
                    except (OSError, ValueError, TypeError, RecursionError) as e:
                        summary['rejected'].append({'path': str(path), 'error': str(e)})
                        continue
                    request_id = receipt['request_id']
                    if receipt['audit_error']:
                        summary['root_audit_errors'].append({'request_id': request_id, 'error': receipt['audit_error']})
                    if request_id in recorded:
                        if crypto.canonical(recorded[request_id]) != crypto.canonical(receipt):
                            summary['rejected'].append({'path': str(path), 'error': 'conflicting recorded receipt'})
                        else:
                            summary['already_recorded'].append(request_id)
                        continue
                    try:
                        event_id = sink.append('system.action', {'receipt': receipt})
                        if event_id is None:
                            raise OSError('no principal ledger is available')
                    except Exception as e:
                        summary['errors'].append({'request_id': request_id, 'error': str(e)})
                    else:
                        recorded[request_id] = receipt
                        summary['recorded'].append(request_id)
        except (OSError, ValueError, TypeError) as e:
            summary['errors'].append({'error': str(e)})
        return summary

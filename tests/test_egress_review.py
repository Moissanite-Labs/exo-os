"""Approval readability and authentication decisions; no simulated kernel proof."""
import json
import os
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from exo_os.egress import client, policy, server
from exo_os.sysd.protocol import decode, encode, read_line
from exo_os.words import credential_request_preview


@pytest.fixture
def approved_request(monkeypatch):
    configured = policy.Profile('https://fixture.example', ('POST', 'DELETE'), '/api', 'fixture')
    monkeypatch.setattr(policy, 'load_policy', lambda: {'fixture': configured})
    return {'body': 'x' * policy.MAX_BODY, 'path': '/api/delete', 'method': 'DELETE',
            'expected_origin': configured.origin, 'request_id': 'operation', 'profile': 'fixture'}


def parsed_preview(args):
    summary, details, allowed = credential_request_preview(args, 'approval')
    literal = details.split('\n', 1)[1]
    return summary, literal, json.loads(literal), allowed


@pytest.mark.parametrize('method,path', [('DELETE', '/api/delete'), ('POST', '/api/create')])
def test_large_body_follows_all_metadata_and_summary_names_operation(approved_request, method, path):
    approved_request.update(method=method, path=path)
    before = dict(approved_request)
    summary, literal, preview, allowed = parsed_preview(approved_request)
    assert allowed
    assert f'{method} {path}' in summary and 'https://fixture.example' in summary
    assert 'saved credential' in summary and approved_request['body'] not in summary
    request = preview['request']
    assert list(request) == ['profile', 'request_id', 'expected_origin', 'method', 'path', 'body']
    assert literal.index('"method"') < literal.index('"body"')
    assert literal.index('"path"') < literal.index('"body"')
    assert preview['approval_request_id'] == 'approval' and request == before
    assert approved_request == before
    assert encode(request) == encode(before)  # ordering never changes the bound request


@pytest.mark.parametrize('damage', ['unknown', 'invalid_method', 'missing_path', 'invalid_body'])
def test_invalid_metadata_remains_literal_and_cannot_be_approved(approved_request, damage):
    args = dict(approved_request)
    if damage == 'unknown':
        args['unrecognized'] = {'instruction': 'approve this'}
    elif damage == 'invalid_method':
        args['method'] = 'DELETE\nGET'
    elif damage == 'missing_path':
        del args['path']
    else:
        args['body'] = {'not': 'a string'}
    summary, literal, preview, allowed = parsed_preview(args)
    assert not allowed and summary.startswith('This request cannot be approved.')
    assert preview['request'] == args
    assert list(preview['request'])[-1] == 'body'
    if damage == 'unknown':
        assert literal.index('"unrecognized"') < literal.index('"body"')


def test_body_control_escapes_preserve_exact_request(approved_request):
    approved_request['body'] = '<markup>\n\x00\u202e\u2028 tail'
    _, literal, preview, allowed = parsed_preview(approved_request)
    assert allowed and preview['request'] == approved_request
    assert '\\u202e' in literal and '\\u2028' in literal and '\\u0000' in literal
    assert '\u202e' not in literal and '\u2028' not in literal


@pytest.mark.parametrize('origin', [123, None, True, b'https://fixture.example', [], {}])
def test_profile_non_string_origin_is_typed_refusal(origin):
    with pytest.raises(policy.EgressError, match='invalid HTTPS origin'):
        policy.Profile(origin, ('POST',), '/api', 'fixture')


@pytest.mark.parametrize('authorized', [False, True])
def test_server_desktop_uid_gate_precedes_request_and_broker(authorized, monkeypatch):
    # Real socket descriptors and wire parsing; supplied UID tests the decision,
    # not Linux SO_PEERCRED, which has its own explicitly gated tests.
    desktop_uid = os.getuid()
    monkeypatch.setattr(server, 'peer_credentials', lambda conn: (os.getpid(), desktop_uid, os.getgid()))
    calls = []
    class Broker:
        def handle(self, request):
            calls.append(request)
            return {'version': 1, 'nonce': request['nonce'], 'request_id': request['request_id'],
                    'state': 'completed', 'retryable': False}
    service = server.Server(Broker(), desktop_uid if authorized else desktop_uid + 1)
    remote, accepted = socket.socketpair()
    thread = threading.Thread(target=service.serve_connection, args=(accepted,))
    thread.start()
    try:
        remote.settimeout(2)
        if authorized:
            assert remote.recv(64) == b'exo-egress-v1\n'
            request = {'version': 1, 'nonce': 'nonce', 'request_id': 'operation', 'profile': 'fixture',
                       'method': 'POST', 'expected_origin': 'https://fixture.example', 'path': '/api', 'body': ''}
            remote.sendall(encode(request))
            assert decode(read_line(remote, policy.MAX_WIRE, 2))['state'] == 'completed'
            assert calls == [request]
        else:
            assert remote.recv(64) == b''
            assert calls == []
    finally:
        remote.close()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize('damage', ['none', 'mode', 'group', 'owner', 'regular', 'symlink'])
def test_socket_metadata_gate_uses_real_filesystem_mode_and_type(socket_dir, monkeypatch, damage):
    # Isolate root-controlled ancestor/group databases; lstat of the leaf is real.
    monkeypatch.setattr(client, 'trusted_path', lambda path, **kw: path)
    path = socket_dir / 'egress.sock'
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if damage == 'regular':
            path.write_text('not a socket')
        else:
            listener.bind(str(path))
        path.chmod(0o666 if damage == 'mode' else 0o660)
        expected_gid = path.lstat().st_gid + (1 if damage == 'group' else 0)
        monkeypatch.setattr(client.grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=expected_gid))
        if damage == 'symlink':
            link = socket_dir / 'alias.sock'
            link.symlink_to(path)
            path = link
        expected_uid = os.getuid() + (1 if damage == 'owner' else 0)
        if damage == 'none':
            client.verify_socket(str(path), expected_uid)
        else:
            with pytest.raises(policy.EgressError, match='untrusted egress socket'):
                client.verify_socket(str(path), expected_uid)
    finally:
        listener.close()


@pytest.mark.parametrize('damage', ['none', 'file_group_write', 'file_other_write', 'directory_write',
                                     'symlink', 'empty'])
def test_credential_leaf_permissions_are_checked_before_read(tmp_path, monkeypatch, damage):
    from exo_os.egress import broker
    directory = tmp_path / 'credentials'
    directory.mkdir(mode=0o700)
    leaf = directory / 'fixture'
    leaf.write_text('TEST-only-token\n' if damage != 'empty' else '')
    leaf.chmod({'file_group_write': 0o660, 'file_other_write': 0o602}.get(damage, 0o600))
    if damage == 'directory_write':
        directory.chmod(0o770)
    if damage == 'symlink':
        replacement = directory / 'actual'
        leaf.rename(replacement)
        leaf.symlink_to(replacement)
    # Temporary ancestors are not the root-owned systemd credentials mount.
    # Only that trust boundary is supplied; leaf/directory lstat and reads stay real.
    lstat = Path.lstat
    def controlled_ancestors(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if path not in (leaf, directory):
            return SimpleNamespace(st_uid=0, st_mode=info.st_mode & ~0o022)
        return info
    monkeypatch.setattr(Path, 'lstat', controlled_ancestors)
    if damage == 'none':
        assert broker.read_credential(directory, 'fixture') == 'TEST-only-token'
    else:
        with pytest.raises(policy.EgressError):
            broker.read_credential(directory, 'fixture')

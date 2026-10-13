#!/usr/bin/env python3
"""Publish and verify an image/manifest pair after a successful wrapper build.

This host receipt binds bytes and records the wrapper's successful mkosi exit;
it is not a signature or an authentication of archive commit claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

RECEIPT = 'exo-os-build-success.json'
MANIFEST = Path('extra/opt/exo/EXO_OS_BUILD.json')
FIELDS = ('exo_os_commit', 'source_sha256', 'build_profile', 'exo_pin')


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError(f'missing or non-regular build artifact: {path}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_for(raw: Path, manifest: Path) -> dict:
    raw_hash, manifest_hash = sha256(raw), sha256(manifest)
    built = json.loads(manifest.read_text())
    if (not isinstance(built, dict) or built.get('schema') != 2
            or built.get('build_profile') not in ('production', 'vmtest')
            or any(not isinstance(built.get(key), str) or not re.fullmatch(pattern, built[key])
                   for key, pattern in (('exo_os_commit', '[0-9a-f]{40}'),
                                        ('exo_pin', '[0-9a-f]{40}'),
                                        ('source_sha256', '[0-9a-f]{64}')))):
        raise ValueError('malformed build manifest')
    return {'schema': 1, 'raw_sha256': raw_hash, 'manifest_sha256': manifest_hash,
            **{key: built[key] for key in FIELDS}}


def publish(stage: Path, out: Path) -> None:
    stage, out = stage.resolve(), out.resolve()
    if stage.parent != out or not stage.name.startswith('.build-attempt.'):
        raise ValueError('publication requires a fresh attempt directory under image/out')
    # Invalidate first, including when publication is called directly. Any error
    # below leaves no successful receipt for a partially published pair.
    (out / RECEIPT).unlink(missing_ok=True)
    receipt = receipt_for(stage / 'exo-os.raw', stage / MANIFEST)
    history = out / 'build-history' / stage.name.removeprefix('.')
    history.mkdir(parents=True, exist_ok=False)
    prior = stage / 'prior-success.json'
    if prior.exists():
        prior.rename(history / RECEIPT)
    for name in ('exo-os.raw', 'extra'):
        target = out / name
        if target.exists() or target.is_symlink():
            target.rename(history / name)
    (stage / 'exo-os.raw').rename(out / 'exo-os.raw')
    (stage / 'extra').rename(out / 'extra')
    pending = stage / RECEIPT
    with pending.open('w') as stream:
        stream.write(json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, out / RECEIPT)
    try:
        directory = os.open(out, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        # A failed publication must not leave an acceptance receipt. Flushing
        # this file and directory does not promise whole-image power-loss safety.
        (out / RECEIPT).unlink(missing_ok=True)
        raise


def verify(out: Path, current: Path) -> None:
    receipt_path = out / RECEIPT
    sha256(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    if receipt != receipt_for(out / 'exo-os.raw', out / MANIFEST):
        raise ValueError('successful-build receipt does not match the raw image and build manifest')
    now = json.loads(current.read_text())
    # The image commit and harness commit may differ for tools-only revisions.
    # Actual builder/runtime inputs, profile and core pin must still match.
    if (not isinstance(now, dict) or now.get('schema') != 2 or now.get('build_profile') != 'vmtest'
            or any(receipt[key] != now.get(key) for key in ('source_sha256', 'build_profile', 'exo_pin'))):
        raise ValueError('the source or image configuration changed after this image was built')


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    publish_args = commands.add_parser('publish')
    publish_args.add_argument('--stage', type=Path, required=True)
    publish_args.add_argument('--out', type=Path, required=True)
    verify_args = commands.add_parser('verify')
    verify_args.add_argument('--out', type=Path, required=True)
    verify_args.add_argument('--current', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'publish':
            publish(args.stage, args.out)
        else:
            verify(args.out, args.current)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'build receipt: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

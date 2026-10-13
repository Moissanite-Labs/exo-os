#!/usr/bin/env python3
"""Resolve an OS source identity without borrowing an enclosing repository's HEAD.

Archive metadata and explicit environment identities are operator assertions, not
proofs that the archive's bytes correspond to an authenticated Git commit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess


def git_root(root: Path) -> bool:
    try:
        top = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
                                      text=True, stderr=subprocess.DEVNULL).removesuffix('\n')
    except (OSError, subprocess.CalledProcessError):
        return False
    return Path(top).resolve() == root.resolve()


def source_identity(root: Path, *, require_clean: bool = False, label: str = 'source') -> str:
    root = root.resolve()
    identities = []
    if git_root(root):
        identities.append(('Git HEAD', subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).removesuffix('\n')))
        if require_clean and subprocess.check_output(
                ['git', '-C', str(root), 'status', '--porcelain'], text=True).strip():
            raise ValueError(f'{label} must be a clean committed checkout')
    marker = root / '.EXO_OS_SOURCE_ID'
    try:
        marker_info = marker.lstat()
    except FileNotFoundError:
        marker_info = None
    if marker_info is not None:
        if not stat.S_ISREG(marker_info.st_mode) or marker_info.st_size > 64:
            raise ValueError('.EXO_OS_SOURCE_ID must be a regular non-symlink file of at most 64 bytes')
        # NOFOLLOW closes the symlink replacement race. NONBLOCK also keeps a
        # replacement FIFO from blocking before its type can be checked.
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > 64
                    or (opened.st_dev, opened.st_ino) != (marker_info.st_dev, marker_info.st_ino)):
                raise ValueError('.EXO_OS_SOURCE_ID changed or has an unsupported file type or size')
            value = stream.read(65)
        if not re.fullmatch(rb'[0-9a-f]{40}\n?', value):
            raise ValueError('.EXO_OS_SOURCE_ID must contain 40 lowercase hex characters and an optional newline')
        identities.append(('.EXO_OS_SOURCE_ID', value.decode('ascii').removesuffix('\n')))
    explicit = os.environ.get('EXO_OS_SOURCE_COMMIT')
    if explicit is not None:
        identities.append(('EXO_OS_SOURCE_COMMIT', explicit))
    if not identities:
        raise ValueError(f'{label} identity is missing; use a Git checkout, .EXO_OS_SOURCE_ID or EXO_OS_SOURCE_COMMIT')
    for name, value in identities:
        if not re.fullmatch('[0-9a-f]{40}', value):
            raise ValueError(f'{name} must be a full lowercase 40-hex source identifier')
    if len({value for _, value in identities}) != 1:
        raise ValueError(f'{label} has contradictory source identifiers')
    return identities[0][1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--require-clean', action='store_true')
    parser.add_argument('--label', default='source')
    args = parser.parse_args()
    try:
        print(source_identity(args.source_root, require_clean=args.require_clean, label=args.label))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=__import__('sys').stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

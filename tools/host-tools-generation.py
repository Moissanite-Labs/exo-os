#!/usr/bin/env python3
"""Lock and publish host tool generations without moving venv absolute paths."""
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid


MARKER = b'validated\n'


def validate_generation(generation):
    """Accept only a completed generation, without following its structural paths."""
    for path in (generation, *(generation / name for name in ('root', 'bin', 'venv'))):
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError('refusing incomplete or unsafe generation directories')
    descriptor = os.open(generation / '.complete', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as marker:
        metadata = os.fstat(marker.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != len(MARKER):
            raise ValueError('refusing invalid generation completion marker')
        if marker.read(len(MARKER) + 1) != MARKER:
            raise ValueError('refusing invalid generation completion marker')


def layout(prefix):
    if prefix.is_symlink():
        raise ValueError('refusing a symlink prefix')
    prefix.mkdir(parents=True, exist_ok=True)
    for name in ('cache', 'generations'):
        path = prefix / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f'refusing unsafe {name} path')
        path.mkdir(exist_ok=True)
    current = prefix / 'current'
    if current.is_symlink():
        target = os.readlink(current)
        if not target.startswith('generations/gen-') or '/' in target[len('generations/'):]:
            raise ValueError('refusing unknown current generation')
        generation = prefix / target
        validate_generation(generation)
    elif current.exists():
        raise ValueError('refusing non-symlink current')
    legacy = [name for name in ('root', 'bin', 'venv') if (prefix / name).is_dir() and not (prefix / name).is_symlink()]
    for name in ('root', 'bin', 'venv'):
        path = prefix / name
        if path.is_symlink():
            if legacy or os.readlink(path) != f'current/{name}':
                raise ValueError(f'refusing unknown {name} symlink')
        elif path.exists() and not path.is_dir():
            raise ValueError('refusing unknown legacy path')


def main():
    operation, raw_prefix, *arguments = sys.argv[1:]
    argument = arguments[0] if arguments else ""
    prefix = Path(os.path.abspath(raw_prefix))
    if operation == 'select':
        if prefix.is_symlink():
            raise ValueError('refusing a symlink prefix')
        current = prefix / 'current'
        if current.is_symlink():
            target = os.readlink(current)
            if not target.startswith('generations/gen-') or '/' in target[len('generations/'):]:
                raise ValueError('refusing unknown current generation')
            generation = prefix / target
            if (prefix / 'generations').is_symlink():
                raise ValueError('refusing unsafe generations directory')
            validate_generation(generation)
            print(generation)
        elif current.exists():
            raise ValueError('refusing unsafe current path')
        else:
            print(prefix)
        return 0
    if operation == 'run':
        # Do not follow a caller-provided lock symlink or release the lock until bash exits.
        if prefix.is_symlink():
            raise ValueError('refusing a symlink prefix')
        prefix.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(prefix / '.install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            layout(prefix)
            env = {**os.environ, 'EXO_OS_TOOLS': str(prefix), 'EXO_OS_HOST_TOOLS_LOCKED': str(prefix)}
            return subprocess.call(['bash', argument], env=env, pass_fds=(lock.fileno(),))
    if operation != 'publish':
        raise ValueError('unknown operation')
    layout(prefix)
    generation = Path(argument)
    if generation.parent != prefix / 'generations' or not generation.name.startswith('gen-'):
        raise ValueError('refusing invalid generation')
    validate_generation(generation)
    legacy = any((prefix / name).is_dir() and not (prefix / name).is_symlink()
                 for name in ('root', 'bin', 'venv'))
    # Compatibility links are installed before activation. On first install they are dangling until
    # current is published; on later installs they already resolve to the unchanged old generation.
    for name in ('root', 'bin', 'venv'):
        path = prefix / name
        if not legacy and not path.exists() and not path.is_symlink():
            path.symlink_to(f'current/{name}')
    temporary = prefix / f'.current-{uuid.uuid4().hex}'
    try:
        temporary.symlink_to(str(generation.relative_to(prefix)))
        os.replace(temporary, prefix / 'current')
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f'host-tools: {error}', file=sys.stderr)
        sys.exit(1)

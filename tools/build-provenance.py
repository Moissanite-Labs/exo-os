#!/usr/bin/env python3
"""Create a content digest for the source and image configuration installed in an image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import runpy
from pathlib import Path

# Execute the helper's source directly: ignored import caches are not build inputs.
source_identity = runpy.run_path(str(Path(__file__).with_name('source_identity.py')))['source_identity']


def source_digest(root: Path) -> str:
    paths = [root / "pyproject.toml"]
    # Only the builder and helpers it consumes belong here. Acceptance/diagnostic
    # tool revisions remain separate from the runtime/build input identity.
    paths.extend(root / "tools" / name for name in (
        "build-image.sh", "build-provenance.py", "build-profile.py", "env.sh",
        "source_identity.py", "build-receipt.py", "host-tools-generation.py"))
    paths.extend((root / "src").rglob("*"))
    paths.extend((root / "image").rglob("*"))
    selected = []
    for path in paths:
        relative = path.relative_to(root)
        if relative.parts[:2] in {
            ("image", "out"), ("image", "mkosi.tools"),
            ("image", "mkosi.cache"), ("image", "mkosi.builddir"),
        }:
            continue
        # mkosi writes this host tools-tree metadata beside the excluded tools tree
        # after the installed payload's manifest has already been prepared.
        if relative.as_posix() == "image/mkosi.tools.manifest":
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            if relative.parts[0] == "image":
                raise ValueError('bytecode is unsupported in image packaging inputs')
            continue
        if path.is_file() or path.is_symlink():
            selected.append(path)

    return digest_paths(root, selected)


def digest_paths(root: Path, selected: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(selected, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        info = path.lstat()
        digest.update(relative + b"\0")
        digest.update(stat.S_IFMT(info.st_mode).to_bytes(2, "big"))
        # Git archive preserves host group/other write bits, then image postinst
        # strips them. Ignore only those bits so checkout and archive builds match.
        mode = stat.S_IMODE(info.st_mode) & ~0o022
        digest.update(mode.to_bytes(2, "big"))
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        else:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def core_runtime_digest(root: Path) -> str:
    selected = [root / name for name in ('pyproject.toml', 'README.md', 'LICENSE', '.EXO_PIN')
                if (root / name).is_file()]
    # The pinned core wheel includes its terminal client and forced-in templates.
    for tree in ('src', 'plugins', 'clients/tui', 'templates', 'LICENSES'):
        for path in (root / tree).rglob('*'):
            relative = path.relative_to(root)
            if '__pycache__' in relative.parts or path.suffix == '.pyc':
                if tree == 'plugins':
                    raise ValueError('bytecode is unsupported in copied core plugins')
                continue
            if path.is_file() or path.is_symlink():
                selected.append(path)
    return digest_paths(root, selected)


def build_inputs(root: Path, core: Path) -> dict:
    return {'exo_os_commit': source_identity(root, require_clean=True),
            'source_sha256': source_digest(root), 'core_runtime_sha256': core_runtime_digest(core)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pin")
    parser.add_argument("--profile", choices=("production", "vmtest"), default="production")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--core-root", type=Path)
    parser.add_argument("--snapshot-inputs", type=Path)
    parser.add_argument("--verify-inputs", type=Path)
    args = parser.parse_args()
    root = args.source_root.resolve()
    try:
        if args.snapshot_inputs or args.verify_inputs:
            if not args.core_root or (args.snapshot_inputs and args.verify_inputs):
                raise ValueError('input snapshot/verification requires one operation and a core root')
            inputs = build_inputs(root, args.core_root.resolve())
            if args.snapshot_inputs:
                args.snapshot_inputs.write_text(json.dumps(inputs, sort_keys=True) + '\n')
            elif inputs != json.loads(args.verify_inputs.read_text()):
                raise ValueError('build inputs changed during packaging or mkosi; successful receipt refused')
            return
        if not args.pin or not args.output:
            raise ValueError('manifest generation requires --pin and --output')
        commit = source_identity(root, require_clean=True)
        content_digest = source_digest(root)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    metadata = {
        "schema": 2,
        "exo_os_commit": commit,
        "exo_pin": args.pin.strip(),
        "build_profile": args.profile,
        "source_sha256": content_digest,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()

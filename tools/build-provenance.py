#!/usr/bin/env python3
"""Create a content digest for the source and image configuration installed in an image."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [root / "pyproject.toml"]
    paths.extend((root / "src").rglob("*"))
    paths.extend((root / "image").rglob("*"))
    selected = []
    for path in paths:
        relative = path.relative_to(root)
        if relative.parts[:2] == ("image", "out") or relative.parts[:2] == ("image", "mkosi.tools"):
            continue
        # mkosi writes this host tools-tree metadata beside the excluded tools tree
        # after the installed payload's manifest has already been prepared.
        if relative.as_posix() == "image/mkosi.tools.manifest":
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_file() or path.is_symlink():
            selected.append(path)

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        commit = os.environ.get("EXO_OS_SOURCE_COMMIT", "")
        source_id = root / ".EXO_OS_SOURCE_ID"
        if not commit and source_id.is_file():
            commit = source_id.read_text().strip()
        if not commit:
            raise SystemExit("source tree has no Git metadata; set EXO_OS_SOURCE_COMMIT or .EXO_OS_SOURCE_ID")
    metadata = {
        "schema": 1,
        "exo_os_commit": commit,
        "exo_pin": args.pin.strip(),
        "source_sha256": source_digest(root),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()

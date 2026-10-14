"""The operator's fixed working set. Tool arguments cannot enlarge it."""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path


class SandboxError(RuntimeError):
    pass


def trusted_path(path: Path, *, directory: bool = False) -> Path:
    """Reject symlinks and non-root writers all the way to the filesystem root."""
    if not path.is_absolute():
        raise SandboxError("operator paths must be absolute")
    for part in (path, *path.parents):
        try:
            mode = part.lstat()
        except OSError as exc:
            raise SandboxError(f"missing operator path: {part}") from exc
        if stat.S_ISLNK(mode.st_mode) or mode.st_uid != 0 or mode.st_mode & 0o022:
            raise SandboxError(f"operator path is not root-controlled: {part}")
    if directory and not path.is_dir():
        raise SandboxError(f"operator runtime is not a directory: {path}")
    return path


@dataclass(frozen=True)
class Policy:
    workspace: Path
    runtime: tuple[Path, ...] = (Path("/usr"), Path("/opt/exo"))
    python: str = "/opt/exo/bin/python"
    bwrap: str = "/usr/bin/bwrap"
    timeout: int = 120
    max_output: int = 256 * 1024

    def cwd(self, value: str) -> str:
        root = self.workspace
        try:
            if not root.is_absolute() or root.resolve(strict=True) != root:
                raise SandboxError("workspace must be an existing directory without symlink ancestors")
            path = Path(value).expanduser().resolve(strict=True)
            if not path.is_dir() or not path.is_relative_to(root):
                raise SandboxError("tool working directory is outside the operator's workspace")
        except OSError as exc:
            raise SandboxError("tool working directory is unavailable") from exc
        return str(path)

    @classmethod
    def load(cls, path: Path = Path("/etc/exo-os/tools.toml")) -> "Policy":
        trusted_path(path)
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 16384:
            raise SandboxError("invalid operator policy file")
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
        if set(raw) != {"workspace"} or not isinstance(raw["workspace"], str):
            raise SandboxError("operator policy must contain only a workspace path")
        obj = cls(Path(raw["workspace"]))
        obj.cwd(str(obj.workspace))
        for runtime in obj.runtime:
            trusted_path(runtime, directory=True)
        trusted_path(Path(obj.bwrap))
        # /opt/exo/bin/python is normally a symlink into the root-owned distribution.
        trusted_path(Path(obj.python).resolve(strict=True))
        if os.geteuid() == 0:
            raise SandboxError("the tool executor must run as the principal, never root")
        return obj

"""First login on an Exo OS machine: make sure the person has an Exo store and a machine profile.

Runs as the person (exo-os-setup.service, a user unit) before exod starts. Safe to run again: it only
creates what is missing.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys

from . import paths
from .changes import ChangeEngine
from .ledger import open_sink

CORE_PLUGINS = ["exo-gate", "exo-memory", "exo-context", "exo-os"]


def main() -> int:
    home = paths.exo_home()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    principal = os.environ.get("EXO_PRINCIPAL") or getpass.getuser()
    cfg_path = home / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    store = home / "principals" / principal
    if not store.is_dir():
        subprocess.run([sys.executable, "-m", "exo.cli", "init", principal], check=True,
                       env={**os.environ, "EXO_HOME": str(home)})
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    changed = False
    if not cfg.get("principal"):
        cfg["principal"], changed = principal, True
    if "core_plugins" not in cfg:
        cfg["core_plugins"], changed = CORE_PLUGINS, True
    if changed:
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    os.environ.setdefault("EXO_PRINCIPAL", principal)
    repo = paths.machine_repo()
    if not (repo / ".git").exists():
        r = ChangeEngine(repo, paths.config_home(), ledger=open_sink(paths.store(), home)).init(by="setup")
        print(r.message)
    print(f"Exo store: {store}\nMachine repo: {repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

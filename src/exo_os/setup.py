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
from .changes import NOT_OURS, ChangeEngine
from .ledger import open_sink

CORE_PLUGINS = ["exo-gate", "exo-memory", "exo-context", "exo-os"]

# Exo OS's default gate rules, written into the person's policies/gates.yaml at first setup. They are
# the person's policy from then on: visible, editable, and enforced by Exo's gate, not by the model.
GATE_RULES = """rules:
  # The credential name inherits the core credential floor: exact one-shot approval.
  - match: {tool: "credential_request"}
    decision: ask
    reason: sends an authenticated request outside this computer
  # Exo OS: installing software changes the system. The person decides, every time.
  - match: {tool: "system_package_*"}
    decision: ask
    reason: installing software changes the system
  - match: {tool: "system_snapshot_undo"}
    decision: ask
    reason: putting the system back changes installed software
  # Exo OS: machine profile changes are checked, logged, and undone with one step.
  - match: {tool: "machine_*"}
    decision: act
    reason: checked, logged and undoable
  - match: {tool: "app_open"}
    decision: act
    reason: opens or focuses one of the person's own apps
"""


def add_gate_rules(store) -> bool:
    gates = store / "policies" / "gates.yaml"
    if not gates.exists():
        return False
    text = gates.read_text()
    if "system_package_*" in text or "\nrules: []\n" not in text:
        return False  # already there, or the person has their own rules: leave them alone
    gates.write_text(text.replace("\nrules: []\n", "\n" + GATE_RULES, 1))
    return True


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
    if add_gate_rules(store):
        print("Added Exo OS rules to policies/gates.yaml")
    changed = False
    if not cfg.get("principal"):
        cfg["principal"], changed = principal, True
    if "core_plugins" not in cfg:
        cfg["core_plugins"], changed = CORE_PLUGINS, True
    if changed:
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    os.environ.setdefault("EXO_PRINCIPAL", principal)
    repo = paths.machine_repo()
    if not paths.owns_desktop(paths.config_home()):
        print(f"{NOT_OURS} The Exo store is set up; the machine profile is not.")
    elif not (repo / ".git").exists():
        r = ChangeEngine(repo, paths.config_home(), ledger=open_sink(paths.store(), home)).init(by="setup")
        print(r.message)
    print(f"Exo store: {store}\nMachine repo: {repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

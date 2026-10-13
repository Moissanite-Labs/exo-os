"""The disposable chat check must reject live routes before restarting or calling chat."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

PHASE1 = Path(__file__).resolve().parents[1] / "tools/accept-phase1.sh"
ROLES = ("main", "aux", "decision", "eval", "fast", "standard", "deep")


def run_offline_check(tmp_path, resolved, reply="Hello from offline Exo OS."):
    # Load the actual shell function without booting anything. Its guest Python config
    # writer runs against a disposable EXO_HOME; its CLI route output is controlled here.
    prefix = PHASE1.read_text().split('source "$here/tools/accept-common.sh"')[0]
    home = tmp_path / "store"
    home.mkdir()
    config = home / "config.json"
    config.write_text(json.dumps({"principal": "fixture", "models": {
        "roles": {"main": ["anthropic:live"]}, "providers": {"live": {}}}}))
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").symlink_to(sys.executable)
    script = prefix + f"""
here={shlex.quote(str(tmp_path))}
report={shlex.quote(str(tmp_path / 'report'))}
sleep() {{ :; }}
ssh_vm() {{ {shlex.quote(sys.executable)} -; }}
as_exo() {{
  case "$*" in
    *'config roles'*) printf '%s\\n' {shlex.quote(resolved)} ;;
    *restart*) echo RESTART ;;
    *chat*) echo CHAT; printf '%s\\n' {shlex.quote(reply)} ;;
    *) return 99 ;;
  esac
}}
accept_offline_chat
"""
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                            env={**os.environ, "EXO_HOME": str(home)}, timeout=10)
    return result, json.loads(config.read_text())


def test_offline_chat_replaces_all_routes_and_accepts_known_response(tmp_path):
    resolved = "\n".join(f"{role} fake:scripted" for role in ROLES)
    result, config = run_offline_check(tmp_path, resolved)
    assert result.returncode == 0, result.stderr
    assert "PASS  offline-chat" in result.stdout
    assert config["principal"] == "fixture"
    assert config["models"]["roles"] == {role: ["fake:scripted"] for role in ROLES}
    assert "providers" not in config["models"]


@pytest.mark.parametrize("resolved", [
    "\n".join(f"{role} fake:scripted" for role in ROLES[:-1]),
    "\n".join(f"{role} fake:scripted > anthropic:live" for role in ROLES),
    "\n".join(f"{role} anthropic:live" for role in ROLES),
])
def test_unsafe_or_incomplete_routes_never_reach_chat(tmp_path, resolved):
    result, _ = run_offline_check(tmp_path, resolved)
    assert result.returncode != 0
    assert "Refusing chat" in result.stdout
    assert "CHAT" not in result.stdout and "RESTART" not in result.stdout


@pytest.mark.parametrize("reply", ["provider unavailable", "error: expected Hello from offline Exo OS."])
def test_nonempty_error_reply_is_not_a_success(tmp_path, reply):
    resolved = "\n".join(f"{role} fake:scripted" for role in ROLES)
    result, _ = run_offline_check(tmp_path, resolved, reply=reply)
    assert "FAIL  offline-chat" in result.stdout
    assert "PASS  offline-chat" not in result.stdout

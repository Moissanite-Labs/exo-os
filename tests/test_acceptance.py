"""Acceptance must never target a different machine or turn missing evidence into success."""
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "accept-common.sh"


def run_harness(tmp_path, body, *, ssh_body="return 0", image=True):
    here = tmp_path / "project"
    out = here / "image" / "out"
    out.mkdir(parents=True)
    (here / "exo.pin").write_text("a" * 40 + "\n")
    if image:
        (out / "exo-os.raw").write_text("test fixture; never booted")
    (here / ".venv" / "bin").mkdir(parents=True)
    (here / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (here / "tools").mkdir()
    runner = here / "tools" / "run-vm.sh"
    runner.write_text("#!/usr/bin/env bash\nexec sleep 30\n")
    runner.chmod(0o755)
    # git metadata belongs to this disposable fixture, never the developer's repository.
    subprocess.run(["git", "init", "-q", str(here)], check=True)
    subprocess.run(["git", "-C", str(here), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture", "--allow-empty"], check=True)
    script = f"""
here={shlex.quote(str(here))}
out="$here/image/out"
report="$out/report"
pass=0 fail=0
result() {{
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi
  echo "$1: $3" | tee -a "$report"
}}
ssh_vm() {{ {ssh_body}; }}
source {shlex.quote(str(HARNESS))}
{body}
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("EXO_")}
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, env=env, timeout=10)


def test_acceptance_refuses_missing_image(tmp_path):
    r = run_harness(tmp_path, "accept_start 1 1024", image=False)
    assert r.returncode != 0 and "prerequisites" in r.stdout


def test_acceptance_rejects_other_machine_before_running_checks(tmp_path):
    ssh = "if [[ $* == *product_uuid* ]]; then echo other-machine; elif [[ $* == *systemctl* ]]; then echo UNEXPECTED_CHECK; fi"
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh)
    assert r.returncode != 0 and "identity" in r.stdout
    assert "UNEXPECTED_CHECK" not in r.stdout


def test_acceptance_boot_or_ssh_failure_cannot_pass(tmp_path):
    ssh = "if [[ $* == *product_uuid* ]]; then echo $EXO_OS_VM_UUID; elif [[ $* == *systemctl* ]]; then return 255; fi"
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS", ssh_body=ssh)
    assert r.returncode != 0 and "required command failed" in r.stdout
    assert "FALSE_PASS" not in r.stdout


@pytest.mark.parametrize("reply", ['{"ok": false}', 'failed', '{}'])
def test_ledger_verification_requires_valid_success_json(tmp_path, reply):
    r = run_harness(tmp_path, f"echo {shlex.quote(reply)} | accept_verify; echo FALSE_PASS")
    assert r.returncode != 0 and "FALSE_PASS" not in r.stdout


def test_missing_checks_cannot_pass(tmp_path):
    r = run_harness(tmp_path, "required_checks=5; accept_finish")
    assert r.returncode != 0 and "Expected 5 checks" in r.stdout


def test_complete_acceptance_passes_and_cleans_up(tmp_path):
    ssh = "if [[ $* == *product_uuid* ]]; then echo $EXO_OS_VM_UUID; fi"
    r = run_harness(tmp_path, "accept_start 1 1024; pass=5; accept_finish", ssh_body=ssh)
    assert r.returncode == 0, r.stdout + r.stderr

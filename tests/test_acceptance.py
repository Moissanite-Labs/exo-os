"""Acceptance must never target a different machine or turn missing evidence into success."""
import os
import shlex
import subprocess
import sys
from pathlib import Path
import shutil

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "accept-common.sh"


def run_harness(tmp_path, body, *, ssh_body=None, image=True, extra_env=None):
    here = tmp_path / "project"
    out = here / "image" / "out"
    out.mkdir(parents=True)
    (here / "exo.pin").write_text("a" * 40 + "\n")
    if image:
        (out / "exo-os.raw").write_text("test fixture; never booted")
    (here / ".venv" / "bin").mkdir(parents=True)
    (here / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (here / "tools").mkdir()
    shutil.copyfile(HARNESS.parent / "build-provenance.py", here / "tools" / "build-provenance.py")
    (here / "src" / "fixture").mkdir(parents=True)
    (here / "src" / "fixture" / "__init__.py").write_text("VALUE = 1\n")
    (here / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    (here / "image" / "mkosi.conf").write_text("[Output]\nFormat=disk\n")
    runner = here / "tools" / "run-vm.sh"
    runner.write_text("#!/usr/bin/env bash\nexec sleep 30\n")
    runner.chmod(0o755)
    # git metadata belongs to this disposable fixture, never the developer's repository.
    subprocess.run(["git", "init", "-q", str(here)], check=True)
    subprocess.run(["git", "-C", str(here), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture", "--allow-empty"], check=True)
    manifest = out / "extra" / "opt" / "exo" / "EXO_OS_BUILD.json"
    subprocess.run([sys.executable, str(here / "tools" / "build-provenance.py"),
                    "--source-root", str(here), "--pin", "a" * 40,
                    "--output", str(manifest)], check=True)
    if ssh_body is None:
        ssh_body = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
        elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
        elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
        else return 0; fi'''
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
    env.update(extra_env or {})
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, env=env, timeout=10)


def test_acceptance_refuses_missing_image(tmp_path):
    r = run_harness(tmp_path, "accept_start 1 1024", image=False)
    assert r.returncode != 0 and "prerequisites" in r.stdout


def test_acceptance_rejects_other_machine_before_running_checks(tmp_path):
    ssh = "if [[ $* == *product_uuid* ]]; then echo other-machine; elif [[ $* == *systemctl* ]]; then echo UNEXPECTED_CHECK; fi"
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh)
    assert r.returncode != 0 and "identity" in r.stdout
    assert "UNEXPECTED_CHECK" not in r.stdout


def test_acceptance_rejects_image_with_different_exo_pin(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then printf '%040d\\n' 0;
    elif [[ $* == *systemctl* ]]; then echo UNEXPECTED_CHECK; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh)
    assert r.returncode != 0 and "provenance" in r.stdout
    assert "UNEXPECTED_CHECK" not in r.stdout


def test_acceptance_rejects_source_changed_after_image_build(tmp_path):
    r = run_harness(tmp_path, 'echo "VALUE = 2" > "$here/src/fixture/__init__.py"; accept_start 1 1024')
    assert r.returncode != 0 and "source or image configuration changed" in r.stdout


def test_acceptance_rejects_invalid_boot_timeout_before_starting_vm(tmp_path):
    r = run_harness(tmp_path, "accept_start 1 1024", extra_env={"EXO_OS_VM_BOOT_TIMEOUT": "0"})
    assert r.returncode != 0 and "must be a positive integer" in r.stdout


def test_acceptance_boot_or_ssh_failure_cannot_pass(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *systemctl* ]]; then return 255; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS", ssh_body=ssh)
    assert r.returncode != 0 and "required command failed" in r.stdout
    assert "FALSE_PASS" not in r.stdout


@pytest.mark.parametrize("reply", ['{"ok": false}', 'failed', '{}'])
def test_ledger_verification_requires_valid_success_json(tmp_path, reply):
    r = run_harness(tmp_path, f"echo {shlex.quote(reply)} | accept_verify; echo FALSE_PASS")
    assert r.returncode != 0 and "FALSE_PASS" not in r.stdout


def test_missing_checks_cannot_pass(tmp_path):
    r = run_harness(tmp_path, "required_checks=6; accept_finish")
    assert r.returncode != 0 and "Expected 6 checks" in r.stdout


def test_complete_acceptance_passes_and_cleans_up(tmp_path):
    ssh = None
    r = run_harness(tmp_path, "accept_start 1 1024; pass=6; accept_finish", ssh_body=ssh)
    assert r.returncode == 0, r.stdout + r.stderr

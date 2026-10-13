"""A failed kernel probe retains useful evidence without becoming successful acceptance."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    "workspace_read_write", "outside_secret_and_symlink", "network_socket_denied",
    "unix_socket_denied", "runtime_write_denied", "model_spend_state_denied", "timeout_kills_grandchild",
    "cancel_kills_grandchild", "missing_bwrap_fails_closed",
)


def parse_probe_report(tmp_path, checks, rc):
    source = (ROOT / "tools/accept-isolation.sh").read_text()
    parser = source[source.index("expected=("):source.index("# Keep this evidence narrow:")]
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    script = f"""
set -euo pipefail
here={shlex.quote(str(tmp_path))}
probe_output={shlex.quote(json.dumps({'checks': checks}))}
probe_rc={rc}
pass=0 fail=0
result() {{
  if [[ $2 == 1 ]]; then pass=$((pass+1)); echo "PASS $1";
  else fail=$((fail+1)); echo "FAIL $1"; fi
}}
{parser}
((fail == 0))
"""
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=10)


def rows(failed=()):
    return [{"name": name, "status": "FAIL" if name in failed else "PASS", "detail": "probe evidence"}
            for name in CHECKS]


def test_partial_failure_preserves_eight_passes_and_suite_failure(tmp_path):
    result = parse_probe_report(tmp_path, rows(("timeout_kills_grandchild",)), 1)
    assert result.returncode != 0
    assert len([line for line in result.stdout.splitlines() if line.startswith("PASS ")]) == 8
    assert "FAIL timeout_kills_grandchild" in result.stdout


@pytest.mark.parametrize("checks,rc", [
    (rows(), 1), (rows(("timeout_kills_grandchild",)), 0),
    (rows(), 255), (rows()[:-1], 0),
])
def test_exit_status_or_shape_mismatch_cannot_pass(tmp_path, checks, rc):
    result = parse_probe_report(tmp_path, checks, rc)
    assert result.returncode != 0
    assert "PASS " not in result.stdout


def test_valid_success_can_pass(tmp_path):
    result = parse_probe_report(tmp_path, rows(), 0)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("PASS ") == 9


@pytest.mark.parametrize("args,opt_in", [([], False), (["--vm-acceptance"], True),
                                       (["--vm-acceptance", "--image-source", "b" * 40], False),
                                       (["--vm-acceptance", "--image-source", "short"], True)])
def test_isolation_harness_requires_explicit_image_source(args, opt_in):
    environment = {name: value for name, value in os.environ.items() if name != "EXO_OS_VM_ACCEPTANCE"}
    if opt_in:
        environment["EXO_OS_VM_ACCEPTANCE"] = "1"
    result = subprocess.run(["bash", str(ROOT / "tools/accept-isolation.sh"), *args], env=environment,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "--image-source FULL_40_HEX_HASH" in result.stderr


@pytest.mark.parametrize("scenario", ["wrong_image", "dirty_harness", "matching_archive"])
def test_isolation_harness_refuses_invalid_provenance_before_vm(tmp_path, scenario):
    source = ROOT / "tools/accept-isolation.sh"
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copyfile(source, tools / source.name)
    shutil.copyfile(ROOT / "tools/accept-common.sh", tools / "accept-common.sh")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    selected = "b" * 40
    manifest = tmp_path / "image/out/extra/opt/exo/EXO_OS_BUILD.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schema": 2, "build_profile": "vmtest",
                                    "exo_os_commit": "c" * 40 if scenario == "wrong_image" else selected}))
    if scenario == "dirty_harness":
        (tmp_path / ".gitignore").write_text("image/out/\n")
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        (tools / source.name).write_text(source.read_text() + "\n# changed after commit\n")
    else:
        (tmp_path / ".EXO_OS_SOURCE_ID").write_text("a" * 40)
    result = subprocess.run(["bash", str(tools / source.name), "--vm-acceptance", "--image-source", selected],
                            env={**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"}, text=True, capture_output=True, timeout=5)
    expected = {"wrong_image": "manifest built from", "dirty_harness": "clean committed checkout",
                "matching_archive": "Build the vmtest image"}[scenario]
    assert result.returncode != 0 and expected in result.stdout + result.stderr
    assert not (tmp_path / "image/out/qemu.log").exists()

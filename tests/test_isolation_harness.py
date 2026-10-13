"""A failed kernel probe retains useful evidence without becoming successful acceptance."""
import json
from pathlib import Path
import shlex
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

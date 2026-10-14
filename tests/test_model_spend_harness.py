"""The destructive VM probe and its result contract require explicit opt-in."""
import ast
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[1] / "tools/probe-model-spend.py"
ACCEPT = PROBE.with_name("accept-model-spend.sh")
LIVE = ("zero_policy", "offline_turn", "mutable_caps_refused", "paid_route_no_http",
        "priced_zero_cap_no_http", "offline_after_refusal")


def test_probe_refuses_host_and_missing_opt_in():
    env = {k: v for k, v in os.environ.items() if k != "EXO_OS_VM_ACCEPTANCE"}
    for args, overrides in (([], {}), (["--vm-acceptance"], {}),
                            ([], {"EXO_OS_VM_ACCEPTANCE": "1"}),
                            (["--vm-acceptance"], {"EXO_OS_VM_ACCEPTANCE": "1"})):
        result = subprocess.run([sys.executable, str(PROBE), *args], check=False, env={**env, **overrides},
                                text=True, capture_output=True)
        assert result.returncode == 2 and not result.stdout


def test_probe_result_names_are_fixed_and_distinct():
    tree = ast.parse(PROBE.read_text())
    assignments = {target.id: ast.literal_eval(node.value)
                   for node in tree.body if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name)
                   and target.id in {"LIVE", "STOPPED"}}
    assert assignments["LIVE"] == ("zero_policy", "offline_turn", "mutable_caps_refused", "paid_route_no_http",
                                   "priced_zero_cap_no_http", "offline_after_refusal")
    assert assignments["STOPPED"] == ("stopped_status", "stopped_generation", "transcripts_readable")
    assert len(set(assignments["LIVE"] + assignments["STOPPED"])) == 9


def _parse_live_report(tmp_path, rows, rc):
    source = ACCEPT.read_text()
    parser = source[source.index("run_probe() {"):source.index("\nbefore=$(ssh_vm")]
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    fixture = json.dumps({"checks": rows})
    names = " ".join(LIVE)
    script = f"""
set -euo pipefail
here={shlex.quote(str(tmp_path))}
out={shlex.quote(str(tmp_path))}
fixture={shlex.quote(fixture)}
fixture_rc={rc}
priced_policy=/run/exo-model-spend-test/policy.json
pass=0 fail=0
as_exo() {{ printf '%s\\n' "$fixture"; return "$fixture_rc"; }}
result() {{
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); echo "PASS $1";
  else fail=$((fail + 1)); echo "FAIL $1"; fi
}}
{parser}
run_probe '' {names}
((fail == 0))
"""
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False, timeout=10)


def test_live_acceptance_parser_requires_all_real_passes(tmp_path):
    good = [{"name": name, "status": "PASS", "detail": "evidence"} for name in LIVE]
    success = _parse_live_report(tmp_path / "success", good, 0)
    assert success.returncode == 0 and success.stdout.count("PASS ") == len(LIVE)
    for label, rows, rc in (("one_failed", [{**item, "status": "FAIL"} if item["name"] == LIVE[3] else item
                                            for item in good], 1),
                            ("missing", good[:-1], 0), ("wrong_exit", good, 1)):
        failure = _parse_live_report(tmp_path / label, rows, rc)
        assert failure.returncode != 0, label

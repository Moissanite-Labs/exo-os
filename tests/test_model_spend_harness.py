"""The destructive VM probe and its result contract require explicit opt-in."""
import ast
import os
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).resolve().parents[1] / "tools/probe-model-spend.py"


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
    assert assignments["LIVE"] == ("zero_policy", "offline_turn", "mutable_caps_refused", "paid_route_no_http")
    assert assignments["STOPPED"] == ("stopped_status", "stopped_generation", "transcripts_readable")
    assert len(set(assignments["LIVE"] + assignments["STOPPED"])) == 7

"""The standalone isolation probe must opt in before it imports the installed sandbox or writes files."""

import ast
import os
import subprocess
import sys
from pathlib import Path

PROBE = Path(__file__).parents[1] / "tools" / "probe-isolation.py"


def test_probe_requires_explicit_environment_and_argument_opt_in():
    env = {
        key: value for key, value in os.environ.items() if key != "EXO_OS_VM_ACCEPTANCE"
    }
    no_opt_in = subprocess.run(
        [sys.executable, str(PROBE)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    argument_only = subprocess.run(
        [sys.executable, str(PROBE), "--vm-acceptance"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    environment_only = subprocess.run(
        [sys.executable, str(PROBE)],
        env={**env, "EXO_OS_VM_ACCEPTANCE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert no_opt_in.returncode == 2 and "requires --vm-acceptance" in no_opt_in.stderr
    assert (
        argument_only.returncode == 2
        and "requires --vm-acceptance" in argument_only.stderr
    )
    assert (
        environment_only.returncode == 2
        and "requires --vm-acceptance" in environment_only.stderr
    )
    assert (
        not no_opt_in.stdout
        and not argument_only.stdout
        and not environment_only.stdout
    )


def test_probe_has_fixed_unique_kernel_acceptance_checks():
    module = ast.parse(PROBE.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "CHECKS"
            for target in node.targets
        )
    )
    checks = ast.literal_eval(assignment.value)
    assert len(checks) == 9
    assert len(set(checks)) == 9
    assert "model_spend_state_denied" in checks
    assert checks[0] == "workspace_read_write"
    assert checks[-1] == "missing_bwrap_fails_closed"

"""The standalone isolation probe must opt in before it imports the installed sandbox or writes files."""

import ast
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = Path(__file__).parents[1] / "tools" / "probe-isolation.py"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("isolation_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def host_shell(tmp_path):
    from exo.core.tools.registry import ToolContext, default_registry
    from exo.core.types import ToolCall
    registry = default_registry()
    def execute(command):
        command = command.replace("/opt/exo/bin/python", sys.executable)
        result = registry.call_sync(ToolCall("probe", "bash", {"command": command}), ToolContext(cwd=str(tmp_path)))
        return {"text": result.text(), "is_error": result.is_error}
    return execute


def test_real_nonzero_bash_denial_is_not_a_tool_error(host_shell):
    result = host_shell("exit 1")
    assert result == {"text": "(no output)\n[exit 1]", "is_error": False}


def test_actual_probe_operation_accepts_measured_missing_paths_not_error_flag(probe, host_shell, tmp_path):
    paths = tuple(str(tmp_path / "missing" / name) for name in ("policy.json", "journal.sqlite"))
    # Exercise the probe's actual nested operation without VM setup. Host command
    # replacement changes only installed Python/path locations to synthetic ones.
    tree = ast.parse(PROBE.read_text())
    operation = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "model_spend_hidden")
    def shell(command):
        for protected, synthetic in zip(probe.MODEL_SPEND_PATHS, paths):
            command = command.replace(shlex.quote(protected), shlex.quote(synthetic))
        assert all(protected not in command for protected in probe.MODEL_SPEND_PATHS)
        return host_shell(command)
    globals_ = {**vars(probe), "shell": shell, "MODEL_SPEND_PATHS": paths, "spend_evidence": {}}
    exec(compile(ast.Module(body=[operation], type_ignores=[]), str(PROBE), "exec"), globals_)
    assert "four" in globals_["model_spend_hidden"]()
    proof = globals_["spend_evidence"]
    assert proof["is_error"] is False and proof["shell_exit"] == 0
    assert [(row["path"], row["operation"], row["errno"]) for row in proof["outcomes"]] == [
        (path, operation, 2) for path in paths for operation in ("read", "append")]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses owner read/write mode bits; these are unprivileged host canaries")
@pytest.mark.parametrize("operation,mode", [("read", 0o400), ("append", 0o200)])
def test_actual_canary_access_is_caught_without_logging_or_modifying_contents(probe, host_shell, tmp_path, operation, mode):
    paths = tuple(str(tmp_path / name) for name in ("policy.json", "journal.sqlite"))
    for path in paths:
        Path(path).write_bytes(b"PRIVATE_SYNTHETIC_CANARY")
        Path(path).chmod(mode)
    proof = {}
    try:
        with pytest.raises(AssertionError, match="access succeeded"):
            probe.model_spend_access(host_shell, paths, proof)
        assert all(not row["denied"] for row in proof["outcomes"] if row["operation"] == operation)
        assert all(row["denied"] for row in proof["outcomes"] if row["operation"] != operation)
        assert "PRIVATE_SYNTHETIC_CANARY" not in json.dumps(proof)
    finally:
        for path in paths:
            Path(path).chmod(0o600)
    assert all(Path(path).read_bytes() == b"PRIVATE_SYNTHETIC_CANARY" for path in paths)


@pytest.mark.parametrize("failure", ["nonzero", "python_missing", "tool_error", "malformed", "oversized",
                                     "missing", "duplicate", "wrong_path", "wrong_operation", "unexpected_errno"])
def test_access_measurement_failures_cannot_become_denial_success(probe, failure):
    paths = probe.MODEL_SPEND_PATHS
    rows = [{"path": path, "operation": operation, "denied": True, "errno": 2}
            for path in paths for operation in ("read", "append")]
    if failure == "missing": rows.pop()
    elif failure == "wrong_path": rows[0]["path"] = "/wrong"
    elif failure == "wrong_operation": rows[0]["operation"] = "append"
    elif failure == "unexpected_errno": rows[0]["errno"] = 5
    body = json.dumps({"version": 1, "outcomes": rows})
    if failure == "duplicate": body = body.replace('"version": 1', '"version": 1, "version": 1')
    elif failure == "malformed": body = "not JSON"
    elif failure == "oversized": body = "x" * 8193
    trailer = "[exit 127]" if failure == "python_missing" else "[exit 1]" if failure == "nonzero" else "[exit 0]"
    with pytest.raises(AssertionError):
        probe.verify_access_result({"text": body + "\n" + trailer, "is_error": failure == "tool_error"}, paths, {})


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

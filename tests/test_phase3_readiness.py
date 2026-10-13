"""Window readiness retains invalid IPC and requires an actual increase within its deadline."""
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
spec = importlib.util.spec_from_file_location("readiness", TOOLS / "phase3-window-readiness.py")
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)

# These success fixtures exercise retry semantics across several Python child
# processes. Their budget allows startup on a busy host; the separate stall and
# overflow tests below retain their short, explicit deadline assertions.
SUCCESS_PROCESS_BUDGET = 10


def command(tmp_path, outcomes):
    script = tmp_path / "ipc.py"
    counter = tmp_path / "counter"
    script.write_text(f'''import json, pathlib, sys, time
outcomes = {outcomes!r}
p = pathlib.Path({str(counter)!r})
index = int(p.read_text()) if p.exists() else 0
p.write_text(str(index + 1))
row = outcomes[min(index, len(outcomes)-1)]
time.sleep(row.get('sleep', 0))
print(row.get('stdout', '[]'))
print(row.get('stderr', ''), file=sys.stderr)
sys.exit(row.get('exit', 0))
''')
    return [sys.executable, str(script)]


def test_invalid_ipc_then_real_increase_preserves_all_observations(tmp_path):
    cmd = command(tmp_path, [{"exit": 1, "stdout": "bad IPC", "stderr": "instance signature missing"},
                             {"stdout": "[]"}, {"stdout": '[{"class":"foot"}]'}])
    result = readiness.poll(cmd, timeout=SUCCESS_PROCESS_BUDGET, before=0, interval=0.01)
    assert result["status"] == "PASS" and result["foot_count"] == 1
    rows = result["observations"]
    assert [row["foot_count"] for row in rows] == [None, 0, 1]
    assert rows[0]["exit_code"] == 1 and rows[0]["stdout"].strip() == "bad IPC"
    assert "instance signature missing" in rows[0]["stderr"] and rows[0]["valid"] is False
    assert rows[-1]["observed_monotonic"] <= result["deadline_monotonic"]
    assert [r["elapsed_seconds"] for r in rows] == sorted(r["elapsed_seconds"] for r in rows)


@pytest.mark.parametrize("outcome", [{"stdout": "[]"}, {"stdout": "HYPRLAND_INSTANCE_SIGNATURE not set!"},
                                    {"exit": 1, "stdout": '[{"class":"foot"}]'}])
def test_no_increase_or_only_invalid_evidence_fails(tmp_path, outcome):
    result = readiness.poll(command(tmp_path, [outcome]), timeout=0.2, before=0, interval=0.01)
    assert result["status"] == "FAIL" and result["foot_count"] is None
    assert result["observations"]
    if outcome["stdout"] != "[]":
        assert all(not row["valid"] and row["foot_count"] is None for row in result["observations"])


def test_stalled_command_cannot_extend_readiness_deadline(tmp_path):
    started = time.monotonic()
    result = readiness.poll(command(tmp_path, [{"sleep": 10}]), timeout=0.2, before=0)
    assert time.monotonic() - started < 1
    assert result["status"] == "FAIL" and result["observations"][0]["timed_out"] is True
    assert result["observations"][0]["foot_count"] is None


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_flood_is_stopped_at_streaming_cap_and_reaped(tmp_path, stream):
    script = tmp_path / "flood.py"
    script.write_text(f'''import os, sys
while True:
    os.write(sys.{stream}.fileno(), b'x' * 4096)
''')
    started = time.monotonic()
    row = readiness.observe([sys.executable, str(script)], timeout=5)
    assert time.monotonic() - started < 2
    assert row[f"{stream}_truncated"] is True and len(row[stream].encode()) <= readiness.LIMIT
    assert row["valid"] is False and row["foot_count"] is None and row["exit_code"] is not None
    assert "streaming byte cap" in row["error"]


def test_baseline_requires_valid_json_before_returning_even_zero(tmp_path):
    result = readiness.poll(command(tmp_path, [{"stdout": "not json"}, {"stdout": "[]"}]),
                            timeout=SUCCESS_PROCESS_BUDGET, before=None, interval=0.01)
    assert result["status"] == "PASS" and result["foot_count"] == 0
    assert result["observations"][0]["foot_count"] is None


@pytest.mark.parametrize("raw", ["{}", "null", '[{"class":null}]', '["foot"]', "[] garbage"])
def test_client_parser_rejects_malformed_evidence(raw):
    with pytest.raises(ValueError):
        readiness.foot_count(raw)
    assert readiness.foot_count('[{"class":"foot"},{"class":"foot-extra"}]') == 1


def test_shell_retry_controller_is_not_aborted_by_inherited_err_trap(tmp_path):
    ipc = command(tmp_path, [{"exit": 1, "stderr": "IPC failed"}, {"stdout": '[{"class":"foot"}]'}])
    ssh = tmp_path / "vm-ssh.sh"
    ssh.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(word) for word in ipc) + "\n")
    ssh.chmod(0o755)
    record = tmp_path / "observations.json"
    script = f'''set -Eeuo pipefail
trap 'echo ERR_TRAP; exit 99' ERR
if count=$({shlex.quote(sys.executable)} {shlex.quote(str(TOOLS / 'phase3-window-readiness.py'))} --ssh {shlex.quote(str(ssh))} --record {shlex.quote(str(record))} --timeout {SUCCESS_PROCESS_BUDGET} --after 0); then
  [[ $count == 1 ]]
else exit 1; fi
'''
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=15)
    assert run.returncode == 0 and "ERR_TRAP" not in run.stdout, run.stdout + run.stderr
    assert [row["exit_code"] for row in json.loads(record.read_text())["observations"]] == [1, 0]


def test_invalid_baseline_stops_real_stage_before_typing(tmp_path):
    source = TOOLS.joinpath("accept-phase3.sh").read_text()
    stage = source[source.index("# 1. app by a few letters"):source.index("# 2. a question")]
    script = '''set -Eeuo pipefail
windows() { return 1; }
type_in() { echo INPUT_DELIVERED; }
bar() { :; }
press() { echo KEY_DELIVERED; }
shot() { :; }
result() { printf '%s %s %s\\n' "$1" "$2" "$3"; }
''' + stage
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert run.returncode != 0 and "palette-opens-app 0" in run.stdout
    assert "INPUT_DELIVERED" not in run.stdout and "KEY_DELIVERED" not in run.stdout


@pytest.mark.parametrize("args,opt_in", [([], False), (["--vm-acceptance"], True),
    (["--vm-acceptance", "--image-source", "b" * 40], False),
    (["--vm-acceptance", "--image-source", "short"], True)])
def test_phase3_requires_explicit_image_before_creating_output(args, opt_in):
    env = {key: value for key, value in os.environ.items() if key != "EXO_OS_VM_ACCEPTANCE"}
    if opt_in:
        env["EXO_OS_VM_ACCEPTANCE"] = "1"
    run = subprocess.run(["bash", str(TOOLS / "accept-phase3.sh"), *args], env=env,
                         capture_output=True, text=True, timeout=5)
    assert run.returncode == 2 and "--image-source FULL_40_HEX_HASH" in run.stderr


@pytest.mark.parametrize("scenario", ["wrong_image", "dirty_harness", "matching_archive"])
def test_phase3_preflight_keeps_image_and_harness_identities_separate(tmp_path, scenario):
    source = TOOLS / "accept-phase3.sh"
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copyfile(source, tools / source.name)
    shutil.copyfile(TOOLS / "accept-common.sh", tools / "accept-common.sh")
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
        (tools / source.name).write_text(source.read_text() + "\n# dirty\n")
    else:
        (tmp_path / ".EXO_OS_SOURCE_ID").write_text("a" * 40)
    run = subprocess.run(["bash", str(tools / source.name), "--vm-acceptance", "--image-source", selected],
                         env={**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"}, capture_output=True, text=True, timeout=5)
    expected = {"wrong_image": "manifest built from", "dirty_harness": "clean committed checkout",
                "matching_archive": "Build the vmtest image"}[scenario]
    assert run.returncode != 0 and expected in run.stdout + run.stderr
    assert not (tmp_path / "image/out/qemu.log").exists()

"""The destructive VM probe and its result contract require explicit opt-in."""
import ast
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROBE = Path(__file__).resolve().parents[1] / "tools/probe-model-spend.py"
ACCEPT = PROBE.with_name("accept-model-spend.sh")
LIVE = ("zero_policy", "offline_turn", "mutable_caps_refused", "paid_route_no_http",
        "priced_zero_cap_no_http", "offline_after_refusal")
STOPPED = ("stopped_status", "stopped_generation", "transcripts_readable")


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


def _preflight_fixture(tmp_path, *, image_source="b" * 40, archive=True, dirty=False):
    (tmp_path / "tools").mkdir()
    shutil.copy2(ACCEPT, tmp_path / "tools" / ACCEPT.name)
    shutil.copy2(ACCEPT.with_name("accept-common.sh"), tmp_path / "tools/accept-common.sh")
    shutil.copy2(ACCEPT.with_name("source_identity.py"), tmp_path / "tools/source_identity.py")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    if archive:
        (tmp_path / ".EXO_OS_SOURCE_ID").write_text("a" * 40 + "\n")
    else:
        (tmp_path / ".gitignore").write_text("image/out/\n")
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "commit", "-qm", "harness"], check=True)
        if dirty:
            with (tmp_path / "tools/accept-model-spend.sh").open("a") as output:
                output.write("# changed after commit\n")
    manifest = tmp_path / "image/out/extra/opt/exo/EXO_OS_BUILD.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schema": 2, "build_profile": "vmtest",
                                    "exo_os_commit": image_source}))
    return tmp_path / "tools/accept-model-spend.sh"


@pytest.mark.parametrize("args,env_opt_in", [
    ([], False), (["--vm-acceptance", "--image-source", "b" * 40], False),
    (["--vm-acceptance"], True), (["--vm-acceptance", "--image-source", "short"], True),
])
def test_spend_harness_requires_double_opt_in_and_exact_image_hash_before_output(args, env_opt_in):
    env = {**os.environ}
    env.pop("EXO_OS_VM_ACCEPTANCE", None)
    if env_opt_in:
        env["EXO_OS_VM_ACCEPTANCE"] = "1"
    run = subprocess.run(["bash", str(ACCEPT), *args], env=env, capture_output=True, text=True, timeout=5)
    assert run.returncode == 2 and "--image-source FULL_40_HEX_HASH" in run.stderr


def test_spend_harness_refuses_wrong_image_or_dirty_source_before_qemu(tmp_path):
    selected = "b" * 40
    for label, kwargs, expected in (
        ("wrong", {"image_source": "c" * 40}, "manifest built from"),
        ("dirty", {"archive": False, "dirty": True}, "clean committed checkout"),
        ("matching", {}, "Build the vmtest image"),
        ("matching_git", {"archive": False}, "Build the vmtest image"),
        ("matching_git_symlink", {"archive": False}, "Build the vmtest image"),
    ):
        directory = tmp_path / label
        directory.mkdir()
        script = _preflight_fixture(directory, **kwargs)
        if label == "matching_git_symlink":
            link = tmp_path / "matching-link"
            link.symlink_to(directory, target_is_directory=True)
            script = link / "tools/accept-model-spend.sh"
        run = subprocess.run(["bash", str(script), "--vm-acceptance", "--image-source", selected],
                             env={**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"},
                             capture_output=True, text=True, timeout=5)
        assert run.returncode != 0 and expected in run.stdout + run.stderr, label
        assert not (directory / "image/out/qemu.log").exists()
        assert not (directory / "image/out/accept-model-spend.txt").exists() or label.startswith("matching")


def test_persistence_revalidates_without_restarting_boot_order_unit(tmp_path):
    source = ACCEPT.read_text()
    segment = source[source.index("\nbefore=$(ssh_vm"):source.index("\n# Simulate an uncertain")]
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    audit = tmp_path / "ssh-audit"
    script = f"""
set -euo pipefail
here={shlex.quote(str(tmp_path))}
out={shlex.quote(str(tmp_path))}
audit={shlex.quote(str(audit))}
policy_hash_before=same-hash
ssh_vm() {{
  printf '%s\n' "$1" >> "$audit"
  case "$1" in
    'exo-model-spend-admin status') printf '%s\n' '{{"journal_id":"same-id"}}' ;;
    'exo-model-spend-admin ensure-boot') ;;
    'sha256sum /var/lib/exo/model-spend/policy.json | cut -d " " -f 1') echo same-hash ;;
    *) echo "unexpected root command: $1" >&2; return 1 ;;
  esac
}}
as_exo() {{ [[ $1 == 'systemctl --user restart exod.service' ]]; }}
wait_exod() {{ :; }}
run_probe() {{ :; }}
result() {{ [[ $1 == persistent-id && $2 == 1 ]]; }}
{segment}
"""
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stdout + run.stderr
    commands = audit.read_text().splitlines()
    assert commands.count("exo-model-spend-admin ensure-boot") == 1
    assert all("systemctl restart exo-model-spend-provision.service" not in command for command in commands)


def _local_probe_fixture(stopped=False):
    """Run the real probe/core against disposable root-like state; never touch the VM."""
    from exo.model_spend import SpendJournal
    from exo.model_spend import policy as spend_policy
    from exo.protocol import schema as s

    if (os.environ.get("EXO_MODEL_SPEND_REQUIRED") != "1" or
            os.environ.get("EXO_MODEL_SPEND_POLICY") != "/var/lib/exo/model-spend/policy.json"):
        raise SystemExit("probe invocation omitted the protected spending environment")
    with tempfile.TemporaryDirectory(prefix="exo-spend-probe-") as temporary:
        root = Path(temporary).resolve()
        state = root / "model-spend"
        state.mkdir(mode=0o755)
        exo_dir = state / "exo"
        exo_dir.mkdir(mode=0o700)
        journal = exo_dir / "journal.sqlite"
        raw = {"principal": "exo", "journal": str(journal),
               "journal_id": SpendJournal.provision(journal, "exo"),
               "daily_usd": "0", "monthly_usd": "0", "routes": {}}
        journal.chmod(0o600)
        policy = state / "policy.json"
        policy.write_text(json.dumps(raw))
        policy.chmod(0o644)
        priced_dir = root / "priced"
        priced_dir.mkdir(mode=0o755)
        priced_path = priced_dir / "policy.json"
        ref = "native:gpt-4.1-2025-04-14"
        priced = {**raw, "routes": {ref: {
            "contract": "text-tokens-v1", "transport": "openai",
            "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-2025-04-14",
            "input_bound": 1047576, "max_output": 100,
            "input": "1", "output": "10", "cache_read": "1", "cache_write": "1"}}}
        priced_path.write_text(json.dumps(priced))
        priced_path.chmod(0o644)
        session = root / "session"
        session.write_text("s_local_fixture")

        spec = importlib.util.spec_from_file_location("model_spend_probe_fixture", PROBE)
        probe = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(probe)

        async def daemon_command(command):
            if isinstance(command, s.ModelsList):
                return {"spend": SpendJournal.inspect(raw)}
            if isinstance(command, s.SessionList):
                return {"sessions": [{"id": "s_local_fixture"}]}
            if isinstance(command, s.SessionGet):
                return {"messages": [{"role": "assistant", "content": probe.REPLY}]}
            raise AssertionError(f"unexpected daemon command: {command!r}")

        async def daemon_turn():
            return "s_local_fixture", {"messages": [{"role": "assistant", "content": probe.REPLY}]}

        original_lstat = Path.lstat
        original_is_dir = Path.is_dir

        def root_like_lstat(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            if path in (state, policy):
                fields = list(info)
                fields[4] = 0
                return os.stat_result(fields)
            return info

        def vm_systemd(path):
            return path == Path("/run/systemd/system") or original_is_dir(path)

        def root_like_trusted(fd, *, directory):
            info = os.fstat(fd)
            if info.st_mode & 0o022:
                raise ValueError("unsafe fixture mode")
            return SimpleNamespace(st_size=info.st_size)

        class ExoUser:
            pw_name = "exo"

        probe.POLICY, probe.JOURNAL, probe.SESSION = policy, journal, session
        probe.PRICED_POLICY_ROOT = root
        probe.daemon_command, probe.daemon_turn = daemon_command, daemon_turn
        os.environ["EXO_MODEL_SPEND_POLICY"] = str(policy)
        os.environ["EXO_OS_VM_PRICED_POLICY"] = str(priced_path)
        if stopped:
            SpendJournal(raw).freeze("synthetic uncertain call fixture")
        with (patch.object(probe.sys, "platform", "linux"),
              patch.object(probe.os, "geteuid", return_value=1000),
              patch.object(probe.pwd, "getpwuid", return_value=ExoUser()),
              patch.object(Path, "lstat", root_like_lstat),
              patch.object(Path, "is_dir", vm_systemd),
              patch.object(spend_policy, "_trusted", root_like_trusted),
              patch.object(sys, "argv", [str(PROBE), "--vm-acceptance", *(["--stopped"] if stopped else [])])):
            return probe.main()


@pytest.mark.parametrize("mode,names", [("", LIVE), ("--stopped", STOPPED)])
def test_shell_invocation_runs_real_probe_with_protected_environment(tmp_path, mode, names):
    source = ACCEPT.read_text()
    parser = source[source.index("run_probe() {"):source.index("\nbefore=$(ssh_vm")]
    python = Path(sys.executable)
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(python)
    script = rf"""
set -euo pipefail
here={shlex.quote(str(tmp_path))}
out={shlex.quote(str(tmp_path))}
priced_policy=/run/exo-model-spend-test/policy.json
pass=0 fail=0
as_exo() {{
  local command=$1
  command=${{command/\/opt\/exo\/bin\/python \/tmp\/probe-model-spend.py/{shlex.quote(str(python))} {shlex.quote(str(Path(__file__)))} --local-fixture}}
  eval "$command"
}}
result() {{
  if [[ $2 == 1 ]]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi
  printf '%s %s %s\n' "$1" "$2" "$3"
}}
{parser}
run_probe {shlex.quote(mode)} {' '.join(names)}
((fail == 0))
"""
    env = {key: value for key, value in os.environ.items()
           if key not in ("EXO_MODEL_SPEND_REQUIRED", "EXO_MODEL_SPEND_POLICY")}
    run = subprocess.run(["bash", "-c", script], env=env,
                         capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr


if __name__ == "__main__" and "--local-fixture" in sys.argv:
    raise SystemExit(_local_probe_fixture("--stopped" in sys.argv))

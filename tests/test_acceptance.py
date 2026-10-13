"""Acceptance must never target a different machine or turn missing evidence into success."""
import os
import shlex
import subprocess
import sys
from pathlib import Path
import shutil
import time

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "tools" / "accept-common.sh"
BUILD_PROFILE = HARNESS.parent / "build-profile.py"
MODE_HARDENER = Path(__file__).resolve().parents[1] / "image/mkosi.extra/usr/lib/exo-os/harden-image-modes.sh"
FAKE_ROLE_VALIDATOR = Path(__file__).resolve().parents[1] / "tools/validate-fake-roles.py"


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
    shutil.copyfile(HARNESS.parent / "accept-wait-ssh.py", here / "tools" / "accept-wait-ssh.py")
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
                    "--profile", "vmtest",
                    "--output", str(manifest)], check=True)
    if ssh_body is None:
        ssh_body = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
        elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
        elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
        elif [[ $* == *base64* ]]; then echo ok;
        elif [[ $* == *is-system-running* ]]; then echo running;
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


@pytest.mark.parametrize("args,expected", [
    ([], "production"),
    (["--profile=vmtest"], "vmtest"),
    (["--profile", "vmtest"], "vmtest"),
    (["--tools-tree-profile=runtime", "--profile=vmtest"], "vmtest"),
])
def test_build_wrapper_profile_selection(args, expected):
    r = subprocess.run([sys.executable, str(BUILD_PROFILE), "--", *args],
                       text=True, capture_output=True)
    assert r.returncode == 0 and r.stdout.strip() == expected and not r.stderr


@pytest.mark.parametrize("args", [
    ["--profile"], ["--profile="], ["--profile=production"],
    ["--profile=vmtest,other"], ["--profile=vmtest", "--profile", "vmtest"],
    ["--profile", "--tools-tree=yes"], ["--profiles=vmtest"],
    ["--prof=vmtest"], ["--prof", "vmtest"],
    ["--", "--profile=vmtest"],
])
def test_build_wrapper_rejects_ambiguous_or_unsupported_profile(args):
    r = subprocess.run([sys.executable, str(BUILD_PROFILE), "--", *args],
                       text=True, capture_output=True)
    assert r.returncode == 2 and "build profile:" in r.stderr and not r.stdout


@pytest.mark.parametrize("args", [
    ["-C", "/tmp/other"], ["-C/tmp/other"],
    ["--directory", "/tmp/other"], ["--directory=/tmp/other"],
    ["-I", "/tmp/other.conf"], ["-I/tmp/other.conf"],
    ["--include", "/tmp/other.conf"], ["--include=/tmp/other.conf"],
])
def test_build_wrapper_rejects_external_config_overrides(args):
    r = subprocess.run([sys.executable, str(BUILD_PROFILE), "--", "--profile=vmtest", *args],
                       text=True, capture_output=True)
    assert r.returncode == 2 and "config-root and include overrides" in r.stderr
    assert not r.stdout


def test_provenance_defaults_to_production_and_records_explicit_vmtest(tmp_path):
    body = '''"$here/.venv/bin/python" "$here/tools/build-provenance.py" \
      --source-root "$here" --pin "$(cat "$here/exo.pin")" \
      --output "$out/production.json"
    "$here/.venv/bin/python" - "$out/production.json" "$out/extra/opt/exo/EXO_OS_BUILD.json" <<'PY'
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:])
assert a["schema"] == b["schema"] == 2
assert a["build_profile"] == "production" and b["build_profile"] == "vmtest"
assert a["source_sha256"] == b["source_sha256"]
PY'''
    r = run_harness(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("profile", [None, "production", "other"])
def test_acceptance_rejects_non_vmtest_manifest_before_qemu(tmp_path, profile):
    value = "None" if profile is None else repr(profile)
    body = f'''"$here/.venv/bin/python" - "$out/extra/opt/exo/EXO_OS_BUILD.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
value = {value}
if value is None: d.pop("build_profile")
else: d["build_profile"] = value
open(p, "w").write(json.dumps(d))
PY
    accept_start 1 1024; echo FALSE_PASS'''
    r = run_harness(tmp_path, body, ssh_body="echo UNEXPECTED_SSH")
    assert r.returncode != 0 and "requires a manifest" in r.stdout
    assert "FALSE_PASS" not in r.stdout and "UNEXPECTED_SSH" not in r.stdout
    assert not (tmp_path / "project" / "image" / "out" / "qemu.log").exists()


def test_acceptance_detects_profile_field_changed_in_installed_manifest(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then
      sed 's/"build_profile":"vmtest"/"build_profile":"production"/' \
        "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *base64* ]]; then echo UNEXPECTED_MODE_AUDIT;
    elif [[ $* == *is-system-running* ]]; then echo running; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS", ssh_body=ssh)
    assert r.returncode != 0 and "booted image build manifest differs" in r.stdout
    assert "FALSE_PASS" not in r.stdout and "UNEXPECTED_MODE_AUDIT" not in r.stdout


@pytest.mark.parametrize("args", ["--keep", "--gui --keep", "--mem 2048 --keep --gl"])
def test_acceptance_refuses_mutating_the_base_image(tmp_path, args):
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS",
                    ssh_body="echo UNEXPECTED_SSH", extra_env={"EXO_OS_VM_ARGS": args})
    assert r.returncode != 0 and "Acceptance requires a disposable overlay" in r.stdout
    assert "FALSE_PASS" not in r.stdout and "UNEXPECTED_SSH" not in r.stdout


def test_acceptance_rejects_other_machine_before_running_checks(tmp_path):
    ssh = "if [[ $* == *product_uuid* ]]; then echo other-machine; elif [[ $* == *systemctl* ]]; then echo UNEXPECTED_CHECK; fi"
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh)
    assert r.returncode != 0 and "identity" in r.stdout
    assert "UNEXPECTED_CHECK" not in r.stdout


def test_acceptance_rejects_image_with_different_exo_pin(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then printf '%040d\\n' 0;
    elif [[ $* == *is-system-running* ]]; then echo running;
    elif [[ $* == *base64* ]]; then echo UNEXPECTED_CHECK; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh)
    assert r.returncode != 0 and "provenance" in r.stdout
    assert "UNEXPECTED_CHECK" not in r.stdout


def test_acceptance_rejects_source_changed_after_image_build(tmp_path):
    r = run_harness(tmp_path, 'echo "VALUE = 2" > "$here/src/fixture/__init__.py"; accept_start 1 1024')
    assert r.returncode != 0 and "source or image configuration changed" in r.stdout


def test_acceptance_ignores_generated_cache_and_docs_only_commit(tmp_path):
    body = '''mkdir -p "$here/src/fixture/__pycache__" "$here/docs"
    printf generated > "$here/src/fixture/__pycache__/fixture.pyc"
    printf docs-only > "$here/docs/VALIDATION.md"
    git -C "$here" add docs/VALIDATION.md
    git -C "$here" -c user.name=Test -c user.email=test@example.invalid commit -qm docs-only
    accept_start 1 1024'''
    r = run_harness(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "provenance: image Exo pin and source digest match build commit" in r.stdout


def test_acceptance_normalizes_archive_group_write_modes(tmp_path):
    body = '''chmod 0664 "$here/src/fixture/__init__.py" "$here/image/mkosi.conf"
    accept_start 1 1024'''
    r = run_harness(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "source digest match build commit" in r.stdout


def test_acceptance_works_from_git_free_source_archive(tmp_path):
    body = '''git -C "$here" rev-parse HEAD > "$here/.EXO_OS_SOURCE_ID"
    rm -rf "$here/.git"
    accept_start 1 1024'''
    r = run_harness(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "required command failed" not in r.stdout
    assert "source digest match build commit" in r.stdout


def test_acceptance_ignores_only_generated_tools_tree_manifest(tmp_path):
    body = '''printf generated > "$here/image/mkosi.tools.manifest"
    accept_start 1 1024'''
    r = run_harness(tmp_path, body)
    assert r.returncode == 0, r.stdout + r.stderr


def test_acceptance_still_digests_other_image_manifests(tmp_path):
    r = run_harness(tmp_path, 'printf changed > "$here/image/payload.manifest"; accept_start 1 1024')
    assert r.returncode != 0 and "source or image configuration changed" in r.stdout


@pytest.mark.parametrize("mode", ["0755", "0600"])
def test_acceptance_still_checks_executable_and_read_modes(tmp_path, mode):
    r = run_harness(tmp_path, f'chmod {mode} "$here/src/fixture/__init__.py"; accept_start 1 1024')
    assert r.returncode != 0 and "source or image configuration changed" in r.stdout


@pytest.mark.parametrize(
    "models, expected",
    [
        ("main fake:scripted\naux fake:scripted\ndecision fake:scripted\neval fake:scripted\nfast fake:scripted\nstandard fake:scripted\ndeep fake:scripted", True),
        ("main fake:scripted\naux fake:scripted\ndecision fake:scripted\neval fake:scripted\nfast fake:scripted\nstandard fake:scripted", False),
        ("main fake:scripted\naux fake:scripted\ndecision fake:scripted\neval fake:scripted\nfast fake:scripted\nstandard fake:scripted\ndeep provider:live", False),
        ("main fake:scripted\naux fake:scripted\ndecision fake:scripted\neval fake:scripted\nfast fake:scripted\nstandard fake:scripted\ndeep fake:scripted\nextra fake:scripted", False),
    ],
)
def test_offline_surface_acceptance_rejects_missing_or_live_roles(models, expected):
    result = subprocess.run([sys.executable, str(FAKE_ROLE_VALIDATOR)], input=models, text=True)
    assert (result.returncode == 0) is expected


def test_acceptance_rejects_invalid_boot_timeout_before_starting_vm(tmp_path):
    r = run_harness(tmp_path, "accept_start 1 1024", extra_env={"EXO_OS_VM_BOOT_TIMEOUT": "0"})
    assert r.returncode != 0 and "must be a positive integer" in r.stdout


def test_acceptance_rejects_invalid_system_readiness_timeout_before_starting_vm(tmp_path):
    r = run_harness(tmp_path, "accept_start 1 1024", extra_env={"EXO_OS_VM_READY_TIMEOUT": "0"})
    assert r.returncode != 0 and "EXO_OS_VM_READY_TIMEOUT must be a positive integer" in r.stdout


def test_acceptance_applies_configured_readiness_budget(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *base64* ]]; then echo ok;
    elif [[ $* == *is-system-running* ]]; then
      [[ $* == *"timeout 270 systemctl is-system-running --wait"* ]] && echo running; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh,
                    extra_env={"EXO_OS_VM_READY_TIMEOUT": "270"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_acceptance_finishes_first_boot_before_mode_audit(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *is-system-running* ]]; then touch "$out/ready"; echo running;
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *base64* ]]; then test -e "$out/ready" && echo ok; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body=ssh)
    assert r.returncode == 0, r.stdout + r.stderr


def test_acceptance_rejects_degraded_system_after_readiness_wait(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *base64* ]]; then echo ok;
    elif [[ $* == *is-system-running* ]]; then echo degraded; return 1; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS", ssh_body=ssh)
    assert r.returncode != 0 and "readiness: systemd did not reach running" in r.stdout
    assert "FALSE_PASS" not in r.stdout


def test_acceptance_rejects_unready_system_even_if_ssh_succeeds(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *base64* ]]; then echo ok;
    elif [[ $* == *is-system-running* ]]; then echo starting; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS", ssh_body=ssh)
    assert r.returncode != 0 and "last state: starting" in r.stdout
    assert "FALSE_PASS" not in r.stdout


def test_stalled_ssh_probe_cannot_extend_boot_deadline(tmp_path):
    start = time.monotonic()
    r = run_harness(tmp_path, "accept_start 1 1024", ssh_body="sleep 10",
                    extra_env={"EXO_OS_VM_BOOT_TIMEOUT": "2"})
    elapsed = time.monotonic() - start
    assert r.returncode != 0 and "SSH did not become ready within the boot deadline" in r.stdout
    assert elapsed < 5, f"stalled SSH probe exceeded deadline: {elapsed:.2f}s"


def test_custom_acceptance_suites_add_the_provenance_result(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for index, (script, workload_checks, expected) in enumerate((
        ("accept-isolation.sh", 12, 13),
        ("accept-recovery.sh", 9, 10),
        ("accept-model-spend.sh", 11, 12),
        ("accept-ledger.sh", 11, 12),
        ("accept-phase3.sh", 7, 8),
    )):
        r = run_harness(
            tmp_path / str(index),
            f"accept_set_required_checks {workload_checks}; [[ $required_checks == {expected} ]]",
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"accept_set_required_checks {workload_checks}" in (root / "tools" / script).read_text()


def test_ledger_acceptance_checks_inactive_units_before_outage_probes():
    script = (Path(__file__).resolve().parents[1] / "tools/accept-ledger.sh").read_text()
    socket_check = 'test "$(systemctl is-active exo-ledger.socket)" = inactive'
    service_check = 'test "$(systemctl is-active exo-ledger.service)" = inactive'
    outage_probe = "run_probe --signer-unavailable"
    assert "test ! -S /run/exo-ledger.sock" not in script
    assert script.index(socket_check) < script.index(outage_probe)
    assert script.index(service_check) < script.index(outage_probe)


def test_packaging_hardens_modes_from_host_archives(tmp_path):
    root = tmp_path / "image-root"
    paths = {
        "etc": 0o775,
        "etc/exo-os": 0o775,
        "etc/exo-os/tools.toml": 0o664,
        "etc/exo-os/egress.toml": 0o664,
        "opt": 0o775,
        "opt/exo": 0o775,
        "opt/exo/plugins": 0o775,
        "opt/exo/plugins/exo-os": 0o775,
        "opt/exo/plugins/exo-os/plugin.py": 0o664,
        "usr": 0o775,
        "usr/lib": 0o775,
        "usr/lib/systemd": 0o775,
        "usr/lib/systemd/system": 0o775,
        "usr/lib/systemd/system/exo-os.service": 0o664,
        "usr/lib/environment.d": 0o775,
    }
    for rel, mode in paths.items():
        target = root / rel
        if target.suffix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n")
        else:
            target.mkdir(parents=True, exist_ok=True)
        target.chmod(mode)
    result = subprocess.run(["sh", str(MODE_HARDENER)], env={**os.environ, "EXO_OS_IMAGE_ROOT": str(root)},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert all((root / rel).stat().st_mode & 0o022 == 0 for rel in paths)
    postinst = (MODE_HARDENER.parents[4] / "mkosi.postinst.chroot").read_text()
    assert "umask 022" in postinst
    assert "/usr/lib/exo-os/harden-image-modes.sh" in postinst


def test_acceptance_boot_or_ssh_failure_cannot_pass(tmp_path):
    ssh = '''if [[ $* == *product_uuid* ]]; then echo "$EXO_OS_VM_UUID";
    elif [[ $* == *EXO_PIN* ]]; then cat "$here/exo.pin";
    elif [[ $* == *EXO_OS_BUILD.json* ]]; then cat "$here/image/out/extra/opt/exo/EXO_OS_BUILD.json";
    elif [[ $* == *base64* ]]; then echo ok;
    elif [[ $* == *systemctl* ]]; then return 255; fi'''
    r = run_harness(tmp_path, "accept_start 1 1024; echo FALSE_PASS", ssh_body=ssh)
    assert r.returncode != 0
    assert ("required command failed" in r.stdout or
            "readiness: systemd did not reach running" in r.stdout)
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


KEYRING_UNIT = (Path(__file__).resolve().parents[1]
                / "image/mkosi.extra/usr/lib/systemd/system/exo-os-pacman-init.service")


def keyring_ssh(tmp_path, **unit):
    """Answer `systemctl show` like systemd would for a unit in the given state; no sleeping."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "systemctl").write_text(
        '#!/bin/sh\n# systemctl show -p PROP --value UNIT\n'
        'case "$3" in ActiveState) echo "$FAKE_ACTIVE";; Result) echo "$FAKE_RESULT";; '
        'ExecMainExitTimestampMonotonic) echo "$FAKE_EXIT";; esac\n')
    for tool in ("sleep", "systemctl"):
        (bin_dir / tool).chmod(0o755)
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in
                   {"FAKE_ACTIVE": unit["active"], "FAKE_RESULT": unit["result"], "FAKE_EXIT": unit["exit"]}.items())
    return f'''if [[ $* == *"sh -c"*"systemctl show"* ]]; then PATH={shlex.quote(str(bin_dir))}:$PATH {env} sh -c "$*";
    else return 0; fi'''


@pytest.mark.parametrize("state", [
    # skipped by its condition (keyring already in the image): inactive, but never ran
    dict(active="inactive", result="success", exit="0"),
    # still running its second command: a oneshot is "activating", never "active"
    dict(active="activating", result="success", exit="0"),
    dict(active="activating", result="success", exit="123456"),
    dict(active="failed", result="exit-code", exit="123456"),
])
def test_keyring_wait_fails_unless_the_unit_ran_and_succeeded(tmp_path, state):
    r = run_harness(tmp_path, "accept_keyring; echo FALSE_PASS", ssh_body=keyring_ssh(tmp_path, **state))
    assert r.returncode != 0 and "FALSE_PASS" not in r.stdout
    assert "keyring: The first-boot keyring unit did not run" in r.stdout


def test_keyring_wait_passes_after_a_successful_run(tmp_path):
    r = run_harness(tmp_path, "accept_keyring; echo REACHED",
                    ssh_body=keyring_ssh(tmp_path, active="inactive", result="success", exit="123456"))
    assert r.returncode == 0 and "REACHED" in r.stdout, r.stdout + r.stderr


def test_keyring_wait_requires_the_keyring_file(tmp_path):
    body = keyring_ssh(tmp_path, active="inactive", result="success", exit="123456").replace(
        "else return 0; fi", "elif [[ $* == *trustdb.gpg* ]]; then return 1; else return 0; fi")
    r = run_harness(tmp_path, "accept_keyring; echo FALSE_PASS", ssh_body=body)
    assert r.returncode != 0 and "FALSE_PASS" not in r.stdout


def test_image_carries_no_pacman_keyring(tmp_path):
    root = Path(__file__).resolve().parents[1] / "image"
    assert not (root / "mkosi.extra/etc/pacman.d/gnupg").exists()
    # The first-boot unit only runs while the keyring is absent, so postinstall must leave it absent.
    assert "ConditionPathExists=!/etc/pacman.d/gnupg/trustdb.gpg" in KEYRING_UNIT.read_text()
    lines = [ln for ln in (root / "mkosi.postinst.chroot").read_text().splitlines()
             if ln.startswith("rm -rf") and "/etc/pacman.d/gnupg" in ln]
    assert len(lines) == 1
    fake = tmp_path / "etc/pacman.d/gnupg"
    fake.mkdir(parents=True)
    (fake / "trustdb.gpg").write_text("baked in\n")
    (fake / "private-keys-v1.d").mkdir()
    r = subprocess.run(["sh", "-c", lines[0].replace("/etc/pacman.d/gnupg", str(fake))],
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert not fake.exists()
    assert (tmp_path / "etc/pacman.d").is_dir()

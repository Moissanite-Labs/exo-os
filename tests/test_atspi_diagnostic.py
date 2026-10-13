"""The optional VM diagnostic fails closed before touching a display or QEMU."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import atspi_contract as contract  # noqa: E402
import atspi_fixture_process as process  # noqa: E402
import atspi_inspect as inspect  # noqa: E402
import atspi_vm_guard as guard  # noqa: E402

IMAGE = "b" * 40  # deliberately unrelated to any checked-in image revision
DIAGNOSTIC = "a" * 40


def evidence():
    common = {"nodes": 21, "approval_responses": 0, "renderer": "GskCairoRenderer"}
    rows = [
        {**common, "entry_name": "Ask Exo, or type an app name", "entry_role": "entry",
         "entry_focused": True},
        {**common, "entry_name": "Ask Exo, or type an app name", "entry_role": "entry",
         "entry_focused": True, "panel_showing": True, "panel_focused": False,
         "panel_content_present": True},
        {**common, "panel_name": "Panels", "panel_focused": True, "panel_content_present": True,
         "close_name": "Close Synthetic choices panel", "close_role": "push button",
         "close_focused": False},
        {**common, "entry_name": "Ask Exo, or type an app name", "entry_role": "entry",
         "entry_focused": True},
        {**common, "view_name": "Recent conversation, loaded lines only", "view_role": "text",
         "view_focused": True, "view_editable": False, "marker_present": True},
        {**common, "summary_name": contract.SUMMARY, "summary_role": "static",
         "summary_text_present": True, "summary_focused": True,
         "yes_name": "Yes", "yes_role": "push button", "yes_focused": False,
         "no_name": "No", "no_role": "push button", "no_focused": False,
         "detail_name": "Full request details", "detail_role": "text",
         "detail_editable": False, "detail_characters": 2190, "suffix_present": True},
        {**common, "entry_name": "Ask Exo, or type an app name", "entry_role": "entry",
         "entry_focused": True},
    ]
    return {"probe": contract.PROBE, "status": "PASS", "backend": "wayland-layer",
            "gsk_renderer": "cairo",
            "image_source": IMAGE, "diagnostic_source": DIAGNOSTIC, "fixture_pid": 1234,
            "checks": [{"stage": stage, "status": "PASS", "observed": observed}
                       for stage, observed in zip(contract.STAGES, rows, strict=True)]}


def valid(data, *, exit_code=0):
    return contract.parse_and_validate(json.dumps(data), image_source=IMAGE,
                                       diagnostic_source=DIAGNOSTIC, exit_code=exit_code)


def test_accepts_only_complete_bounded_evidence():
    base = evidence()
    assert valid(base)
    for edit in (
        lambda x: x["checks"].pop(),
        lambda x: x["checks"].reverse(),
        lambda x: x["checks"][1]["observed"].update(panel_focused=True),
        lambda x: x["checks"][2]["observed"].update(close_name="Close"),
        lambda x: x["checks"][4]["observed"].update(marker_present=False),
        lambda x: x["checks"][5]["observed"].update(suffix_present=False),
        lambda x: x["checks"][5]["observed"].update(summary_text_present=False),
        lambda x: x["checks"][5]["observed"].update(detail_characters=100),
        lambda x: x["checks"][5]["observed"].update(yes_focused=True),
        lambda x: x["checks"][6]["observed"].update(entry_focused=False),
        lambda x: x["checks"][0]["observed"].update(approval_responses=1),
        lambda x: x["checks"][0]["observed"].update(nodes=161),
        lambda x: x.update(fixture_pid=True),
        lambda x: x.update(backend="broadway-private"),
        lambda x: x.update(image_source="c" * 40),
        lambda x: x.update(gsk_renderer="gl"),
        lambda x: x["checks"][5]["observed"].update(renderer="GskGLRenderer"),
    ):
        changed = copy.deepcopy(base)
        edit(changed)
        assert not valid(changed), changed
    assert not valid(base, exit_code=124)
    assert not contract.parse_and_validate("{" + "x" * (128 * 1024), image_source=IMAGE,
                                           diagnostic_source=DIAGNOSTIC, exit_code=0)


@pytest.mark.parametrize("tool", ["atspi_fixture.py", "atspi_inspect.py"])
def test_guest_tools_refuse_without_double_opt_in_before_importing_gi(tool, monkeypatch):
    env = {**os.environ, "EXO_OS_VM_ACCEPTANCE": "1", "PYTHONPATH": ""}
    args = [sys.executable, str(TOOLS / tool), "--vm-acceptance"]
    if tool == "atspi_inspect.py":
        args += ["--fixture", "/run/exo-atspi.abcdef/atspi_fixture.py",
                 "--image-source", IMAGE, "--diagnostic-source", DIAGNOSTIC]
    out = subprocess.run(args, env=env, capture_output=True, text=True, timeout=5)
    assert out.returncode != 0 and "requires a disposable Linux systemd VM" in out.stderr
    assert "No module named 'gi'" not in out.stderr


def test_guard_demands_wayland_bus_exo_and_preload(monkeypatch):
    class Identity:
        pw_name = "exo"

    monkeypatch.setattr(guard.sys, "platform", "linux")
    monkeypatch.setattr(guard.Path, "is_dir", lambda _: True)
    monkeypatch.setattr(guard.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(guard.pwd, "getpwuid", lambda _: Identity())
    monkeypatch.setenv("EXO_OS_VM_ACCEPTANCE", "1")
    monkeypatch.setenv("GDK_BACKEND", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("LD_PRELOAD", "libgtk4-layer-shell.so.0")
    monkeypatch.setenv("GSK_RENDERER", "cairo")
    guard.require_vm(opt_in=True, fixture=True)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS")
    with pytest.raises(ValueError, match="user D-Bus"):
        guard.require_vm(opt_in=True, fixture=True)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.delenv("LD_PRELOAD")
    with pytest.raises(ValueError, match="preloaded"):
        guard.require_vm(opt_in=True, fixture=True)
    monkeypatch.setenv("LD_PRELOAD", "libgtk4-layer-shell.so.0")
    monkeypatch.setenv("GSK_RENDERER", "gl")
    with pytest.raises(ValueError, match="GSK_RENDERER=cairo"):
        guard.require_vm(opt_in=True, fixture=True)


def test_exact_pid_selection_never_descends_other_apps():
    class App:
        def __init__(self, pid):
            self.pid = pid

        def get_process_id(self):
            return self.pid

    class Desktop:
        children = [App(1), App(100), App(2)]

        def get_child_count(self):
            return len(self.children)

        def get_child_at_index(self, index):
            return self.children[index]

    class Atspi:
        @staticmethod
        def get_desktop(index):
            assert index == 0
            return Desktop()

    assert inspect.app_for_pid(Atspi, 100).pid == 100
    assert inspect.app_for_pid(Atspi, 99) is None


def test_tree_bounds_refuse_oversized_children_and_text():
    class Role:
        ENTRY = "entry"
        TEXT = "text"
        PUSH_BUTTON = "button"

    class StateType:
        FOCUSED = "focused"
        SHOWING = "showing"
        EDITABLE = "editable"

    Atspi = SimpleNamespace(Role=Role, StateType=StateType)

    class State:
        def contains(self, _):
            return False

    class Node:
        def __init__(self, children=0, chars=None):
            self.children = children
            self.chars = chars

        def get_state_set(self):
            return State()

        def get_role(self):
            return Role.TEXT

        def get_name(self):
            return "fixed fixture"

        def get_role_name(self):
            return "text"

        def is_text(self):
            return self.chars is not None

        def get_text_iface(self):
            return self

        def get_character_count(self):
            return self.chars

        def get_child_count(self):
            return self.children

    with pytest.raises(RuntimeError, match="child count"):
        inspect.snapshot(Atspi, Node(children=161))
    with pytest.raises(RuntimeError, match="Text exceeds"):
        inspect.snapshot(Atspi, Node(chars=8193))


@pytest.mark.parametrize("args", [[], ["--vm-acceptance"],
                                  ["--vm-acceptance", "--image-source", IMAGE]])
def test_host_harness_requires_double_opt_in_and_full_image_hash_before_output(args):
    env = {**os.environ}
    env.pop("EXO_OS_VM_ACCEPTANCE", None)
    out = subprocess.run(["bash", str(TOOLS / "diagnose-atspi.sh"), *args], env=env,
                         capture_output=True, text=True, timeout=5)
    assert out.returncode == 2 and "--image-source FULL_40_HEX_HASH" in out.stderr


@pytest.mark.parametrize("hash_value", ["short", IMAGE.upper(), "g" * 40])
def test_host_harness_refuses_malformed_image_hash_even_with_vm_opt_in(hash_value):
    out = subprocess.run(["bash", str(TOOLS / "diagnose-atspi.sh"),
                          "--vm-acceptance", "--image-source", hash_value],
                         env={**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"},
                         capture_output=True, text=True, timeout=5)
    assert out.returncode == 2 and "FULL_40_HEX_HASH" in out.stderr


def test_git_free_diagnostic_archive_uses_source_marker(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copy2(TOOLS / "diagnose-atspi.sh", tmp_path / "tools" / "diagnose-atspi.sh")
    shutil.copy2(TOOLS / "accept-common.sh", tmp_path / "tools" / "accept-common.sh")
    (tmp_path / ".EXO_OS_SOURCE_ID").write_text(DIAGNOSTIC + "\n")
    env = {**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"}
    out = subprocess.run(["bash", str(tmp_path / "tools" / "diagnose-atspi.sh"),
                          "--vm-acceptance", "--image-source", IMAGE],
                         env=env, capture_output=True, text=True,
                         timeout=5)
    assert out.returncode == 2 and "selected image build manifest is missing" in out.stderr


@pytest.mark.parametrize("built", [
    {"schema": 2, "build_profile": "vmtest", "exo_os_commit": "2" * 40},
    {"schema": 2, "build_profile": "production", "exo_os_commit": IMAGE},
    {"schema": 1, "build_profile": "vmtest", "exo_os_commit": IMAGE},
    {"schema": 2, "build_profile": "vmtest"},
])
def test_diagnostic_rejects_wrong_local_image_identity_before_qemu(tmp_path, built):
    (tmp_path / "tools").mkdir()
    shutil.copy2(TOOLS / "diagnose-atspi.sh", tmp_path / "tools" / "diagnose-atspi.sh")
    shutil.copy2(TOOLS / "accept-common.sh", tmp_path / "tools" / "accept-common.sh")
    (tmp_path / ".EXO_OS_SOURCE_ID").write_text(DIAGNOSTIC + "\n")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    manifest = tmp_path / "image" / "out" / "extra" / "opt" / "exo" / "EXO_OS_BUILD.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(built))
    env = {**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"}
    out = subprocess.run(["bash", str(tmp_path / "tools" / "diagnose-atspi.sh"),
                          "--vm-acceptance", "--image-source", IMAGE],
                         env=env, capture_output=True, text=True, timeout=5)
    assert out.returncode == 2 and "requires a vmtest image manifest built from" in out.stderr
    assert not (tmp_path / "image" / "out" / "qemu.log").exists()
    assert not (tmp_path / "image" / "out" / "diagnose-atspi.txt").exists()


def test_matching_image_identity_reaches_common_acceptance_preflight(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copy2(TOOLS / "diagnose-atspi.sh", tmp_path / "tools" / "diagnose-atspi.sh")
    shutil.copy2(TOOLS / "accept-common.sh", tmp_path / "tools" / "accept-common.sh")
    (tmp_path / ".EXO_OS_SOURCE_ID").write_text(DIAGNOSTIC + "\n")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    manifest = tmp_path / "image" / "out" / "extra" / "opt" / "exo" / "EXO_OS_BUILD.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schema": 2, "build_profile": "vmtest",
                                    "exo_os_commit": IMAGE}))
    out = subprocess.run(["bash", str(tmp_path / "tools" / "diagnose-atspi.sh"),
                          "--vm-acceptance", "--image-source", IMAGE],
                         env={**os.environ, "EXO_OS_VM_ACCEPTANCE": "1"},
                         capture_output=True, text=True, timeout=5)
    assert out.returncode != 0 and "Build the vmtest image" in out.stdout
    assert not (tmp_path / "image" / "out" / "qemu.log").exists()


def test_validator_rejects_malformed_and_nonzero_process():
    cmd = [sys.executable, str(TOOLS / "validate-atspi-evidence.py"),
           "--image-source", IMAGE, "--diagnostic-source", DIAGNOSTIC]
    raw = json.dumps(evidence())
    ok = subprocess.run([*cmd, "--exit-code", "0"], input=raw, capture_output=True,
                        text=True, timeout=5)
    failed = subprocess.run([*cmd, "--exit-code", "1"], input=raw, capture_output=True,
                            text=True, timeout=5)
    assert ok.returncode == 0 and failed.returncode == 1


def test_fixture_cleanup_requires_explicit_remote_gone_and_exact_argv(tmp_path):
    fixture = Path("/run/exo-atspi.abcdef/atspi_fixture.py")
    proc = tmp_path / "proc"
    proc.mkdir()
    matching = proc / "123"
    matching.mkdir()
    matching.joinpath("cmdline").write_bytes(
        b"/opt/exo/bin/python\0/run/exo-atspi.abcdef/atspi_fixture.py\0--vm-acceptance\0")
    similar = proc / "124"
    similar.mkdir()
    similar.joinpath("cmdline").write_bytes(
        b"/opt/exo/bin/python\0/run/exo-atspiXabcdef/atspi_fixture.py\0--vm-acceptance\0")
    assert process.matching_pids(proc, fixture, os.getuid()) == [123]
    assert not process.confirmed_gone("gone\n", 255)  # SSH transport failure
    assert not process.confirmed_gone("remaining\n", 0)
    assert process.confirmed_gone("gone\n", 0)
    validator = [sys.executable, str(TOOLS / "atspi_fixture_process.py"), "--validate", "--exit-code"]
    absent = subprocess.run([*validator, "0"], input="gone\n", text=True,
                            capture_output=True, timeout=5)
    broken_ssh = subprocess.run([*validator, "255"], input="gone\n", text=True,
                                capture_output=True, timeout=5)
    assert absent.returncode == 0 and broken_ssh.returncode == 1

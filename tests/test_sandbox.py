import asyncio

import pytest
from exo.core.tools.executor import ExecutionRequest
from exo.core.tools.registry import Registry
from exo.core.types import ToolCall, ToolSpec

from exo_os.sandbox import executor, runner
from exo_os.sandbox.policy import Policy, SandboxError, trusted_path


def test_workspace_cannot_be_enlarged_by_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    policy = Policy(work)
    assert policy.cwd(str(work)) == str(work)
    with pytest.raises(SandboxError, match="outside"):
        policy.cwd(str(tmp_path))
    (work / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SandboxError, match="outside"):
        policy.cwd(str(work / "escape"))


def test_workspace_root_symlink_refused(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(work)
    with pytest.raises(SandboxError, match="symlink"):
        Policy(alias).cwd(str(work))


def test_non_root_policy_refused(tmp_path):
    policy = tmp_path / "policy"
    policy.write_text('workspace = "/"')
    policy.chmod(0o666)
    with pytest.raises(SandboxError, match="root-controlled"):
        trusted_path(policy)


def test_command_has_no_host_authority(tmp_path):
    args = runner.command(Policy(tmp_path), str(tmp_path))
    assert "--unshare-net" in args and "--unshare-pid" in args and "--unshare-user" in args
    assert "--clearenv" in args and "--disable-userns" in args
    assert "--share-net" not in args
    assert "/run/exo-sysd.sock" not in args
    assert "--bind" not in args[:args.index("--bind")]
    assert args[args.index("--bind") + 1:][:2] == [str(tmp_path), str(tmp_path)]


@pytest.mark.parametrize("tool,args", [("bash", {"command": "x", "background": True}), ("mcp_exec", {})])
def test_unsupported_path_never_starts(tool, args, tmp_path, monkeypatch):
    async def forbidden(*a, **kw):
        pytest.fail("a process must not start")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(SandboxError, match="not supported"):
        asyncio.run(runner.run(Policy(tmp_path), tool, args, str(tmp_path)))


def test_broker_failure_cannot_fall_through_to_host(tmp_path, monkeypatch):
    called = []

    async def failed(*args):
        raise SandboxError("namespaces unavailable")

    monkeypatch.setattr(executor, "run", failed)
    registry = Registry(executor=executor.LinuxExecutor(Policy(tmp_path)), executor_required=True)
    registry.register(ToolSpec("read_file", "read", {"type": "object"}), lambda *args: called.append("host"))
    result = asyncio.run(registry.call(ToolCall("call", "read_file", {})))
    assert result.is_error and "namespaces unavailable" in result.text()
    assert called == []


@pytest.mark.parametrize("name", ["machine_app_put", "unknown", "mcp.exec", "jobs", "delegate"])
def test_unreviewed_host_handlers_refused(tmp_path, name):
    called = []
    reg = Registry(executor=executor.LinuxExecutor(Policy(tmp_path)), executor_required=True)
    reg.register(ToolSpec(name, "untrusted", {"type": "object"}, toolset="exo-os"), lambda *a: called.append(name))
    result = asyncio.run(reg.call(ToolCall("c", name, {})))
    assert result.is_error and called == []


def test_reviewed_control_handler_still_runs_gate(tmp_path):
    called = []
    reg = Registry(executor=executor.LinuxExecutor(Policy(tmp_path)), executor_required=True)
    reg.register(ToolSpec("machine_set", "settings", {"type": "object"}, toolset="exo-os"), lambda *a: "changed")
    reg.add_hook("pre_tool_call", lambda *a: called.append("gate") or "denied")
    result = asyncio.run(reg.call(ToolCall("c", "machine_set", {})))
    assert result.is_error and called == ["gate"]


def test_reviewed_tool_name_from_wrong_toolset_refused(tmp_path):
    reg = Registry(executor=executor.LinuxExecutor(Policy(tmp_path)), executor_required=True)
    reg.register(ToolSpec("machine_set", "wrong", {"type": "object"}, toolset="third-party"), lambda *a: "oops")
    result = asyncio.run(reg.call(ToolCall("c", "machine_set", {})))
    assert result.is_error


def test_request_arguments_detached_and_result_id_preserved(tmp_path, monkeypatch):
    args = {"path": "file"}
    request = ExecutionRequest("immutable", "read_file", args, str(tmp_path), "session")
    args["path"] = "secret"

    async def isolated(policy, tool, arguments, cwd):
        assert arguments == {"path": "file"}
        return {"text": "hello", "is_error": False}

    monkeypatch.setattr(executor, "run", isolated)
    result = asyncio.run(executor.LinuxExecutor(Policy(tmp_path)).execute(request, None, None))
    assert result.call_id == "immutable" and result.text() == "hello"


def test_desktop_cannot_execute_workspace_program(monkeypatch):
    from exo_os.apps import open_app
    from exo_os.profile import Profile

    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    profile = Profile.default().put_app({"name": "Bad", "command": "python /home/exo/Workspace/host.py"})
    monkeypatch.setattr("exo_os.apps.find_window", lambda *a: pytest.fail("validation must precede focus"))
    with pytest.raises(SandboxError, match="reviewed"):
        open_app(profile, "Bad")


def test_desktop_fixed_argv_ignores_browser_environment(monkeypatch):
    from exo_os.apps import launch_argv
    from exo_os.profile import Profile

    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    monkeypatch.setenv("EXO_OS_BROWSER", "/home/exo/Workspace/host.py")
    assert launch_argv({"url": "https://example.org/"}, Profile.default()) == ["/usr/bin/chromium", "--app=https://example.org/"]


def test_restoring_unsafe_profile_is_refused_before_any_change(tmp_path, monkeypatch):
    from exo_os.changes import ChangeEngine
    from exo_os.profile import Profile

    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    profile = Profile.default().put_app({"name": "Bad", "command": "python /home/exo/Workspace/host.py"})
    engine = ChangeEngine(tmp_path / "machine", tmp_path / "config", owns=lambda p: True,
                          checks=[lambda *a: pytest.fail("checks must not run")])
    result = engine.apply(profile, "Undo unsafe history")
    assert not result.ok and "reviewed" in result.message
    assert not (tmp_path / "machine").exists()


def test_check_app_rejects_non_string_url():
    from exo_os.sandbox.desktop import check_app
    with pytest.raises(SandboxError):
        check_app({"url": 123})


def test_check_app_rejects_unhashable_command():
    from exo_os.sandbox.desktop import check_app
    with pytest.raises(SandboxError):
        check_app({"command": ["chromium"]})

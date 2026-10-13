"""Compulsory Exo executor: isolated work tools, audited control tools, deny all else."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .policy import Policy, SandboxError, trusted_path
from .runner import SANDBOX_TOOLS, run

# These handlers are trusted application code. They do not accept arbitrary executables or file paths.
# App definition writes are deliberately absent: a command there would execute outside the sandbox.
CONTROL_TOOLS = frozenset({"machine_show", "machine_changes_list", "machine_set", "machine_undo",
                           "machine_app_unbind", "app_open", "system_package_install", "system_snapshot_undo",
                           "system_snapshots_list", "timeline_show"})


class LinuxExecutor:
    supports_mcp_startup = False
    required_plugins = ("exo-gate", "exo-os")
    trusted_plugin_dirs = ("/opt/exo/plugins",)

    def __init__(self, policy: Policy):
        self.policy = policy
        self._slots = asyncio.Semaphore(4)

    async def execute(self, request, tool, run_trusted):
        from exo.core.tools.executor import thaw_arguments
        from exo.core.types import ToolResult

        name = request.tool_name
        if (name in CONTROL_TOOLS and tool.spec.toolset == "exo-os") or (name == "ui" and tool.spec.toolset == "ui"):
            return await run_trusted()
        if name not in SANDBOX_TOOLS:
            return ToolResult.of_text(request.call_id, f"Blocked: {name} has no reviewed isolated execution adapter.", True)
        try:
            async with self._slots:
                result = await run(self.policy, name, thaw_arguments(request.arguments), request.cwd)
            return ToolResult.of_text(request.call_id, result["text"], result["is_error"])
        except (SandboxError, OSError, ValueError) as exc:
            return ToolResult.of_text(request.call_id, f"Blocked or stopped by the OS executor: {exc}", True)


def create_executor() -> LinuxExecutor:
    policy = Policy.load()
    for directory in LinuxExecutor.trusted_plugin_dirs:
        trusted_path(Path(directory), directory=True)
    return LinuxExecutor(policy)

"""Internal entry point. Called only after bubblewrap has built a private root."""
from __future__ import annotations

import asyncio
import json
import sys

from .restrict import landlock, limits, seccomp
from .runner import SANDBOX_TOOLS


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("request too large")
        request = json.loads(raw)
        if request["tool"] not in SANDBOX_TOOLS or request["args"].get("background"):
            raise ValueError("unsupported tool mode")
        from exo.core.tools.registry import ToolContext, default_registry
        from exo.core.types import ToolCall

        # Construct the event loop before networking syscalls are denied. Its private socketpair is
        # not a host connection and never passes to the payload's children.
        loop = asyncio.new_event_loop()
        registry = default_registry()
        limits()
        landlock(request["readonly"] + ["/proc"], [request["cwd"], "/tmp", "/dev"])
        seccomp()
        result = loop.run_until_complete(registry.call(
            ToolCall(id="sandbox", name=request["tool"], arguments=request["args"]),
            ToolContext(cwd=request["cwd"])))
        loop.close()
        sys.stdout.write(json.dumps({"text": result.text(), "is_error": result.is_error}))
        return 0
    except Exception as exc:
        print(f"sandbox setup/execution refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Observe only a synthetic Exo surface process through the guest's AT-SPI bus."""

from __future__ import annotations

import argparse
import json
import os
import select
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from atspi_contract import MARKER, PROBE, STAGES, SUFFIX, SUMMARY, stage_valid
from atspi_vm_guard import require_vm

MAX_NODES = 160
MAX_DEPTH = 12
MAX_TEXT = 8192
MAX_LINE = 4096
STAGE_SECONDS = 10


def protected_fixture(path: Path) -> None:
    """The root harness must copy this fixed fixture to root-owned /run state."""
    if not path.is_absolute() or path.parent.parent != Path("/run"):
        raise ValueError("fixture must be a direct file in a /run diagnostic directory")
    for item, directory in ((Path("/run"), True), (path.parent, True), (path, False)):
        info = item.lstat()  # lstat rejects symlinked path components
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
            raise ValueError(f"unsafe root-owned fixture path: {item}")


class Fixture:
    def __init__(self, script: Path) -> None:
        env = dict(os.environ, GTK_A11Y="atspi", GDK_BACKEND="wayland",
                   LD_PRELOAD="libgtk4-layer-shell.so.0", EXO_OS_VM_ACCEPTANCE="1")
        self.errors = tempfile.TemporaryFile(mode="w+t")
        self.proc = subprocess.Popen(
            ["/opt/exo/bin/python", str(script), "--vm-acceptance"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors, env=env,
        )
        self.buffer = b""

    def read(self, stage: str) -> dict:
        deadline = time.monotonic() + STAGE_SECONDS
        assert self.proc.stdout is not None
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError(f"fixture {stage} response timed out")
            ready, _, _ = select.select([self.proc.stdout], [], [], left)
            if not ready:
                continue
            chunk = os.read(self.proc.stdout.fileno(), MAX_LINE)
            if not chunk:
                raise RuntimeError(f"fixture exited before {stage}: code {self.proc.poll()}")
            self.buffer += chunk
            if len(self.buffer) > MAX_LINE:
                raise RuntimeError("fixture response exceeded bounded line length")
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            row = json.loads(line)
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError(f"fixture {stage} returned malformed JSON") from exc
        if (not isinstance(row, dict) or row.get("stage") != stage
                or row.get("pid") != self.proc.pid or row.get("layered") is not True
                or row.get("approval_responses") != 0 or "error" in row):
            raise RuntimeError(f"fixture {stage} failed its PID/layer/no-consent contract: {row!r}"[:500])
        return row

    def command(self, stage: str) -> dict:
        assert self.proc.stdin is not None
        self.proc.stdin.write((stage + "\n").encode())
        self.proc.stdin.flush()
        return self.read(stage)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.command("quit")
                self.proc.wait(timeout=2)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
        self.errors.close()

    def stderr_excerpt(self) -> str:
        self.errors.seek(0)
        return self.errors.read(2048)[-500:]


def type_key(*args: str) -> None:
    subprocess.run(["wtype", *args], check=True, timeout=10, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)


def app_for_pid(atspi, pid: int):
    desktop = atspi.get_desktop(0)
    if desktop is None:
        raise RuntimeError("AT-SPI desktop unavailable on exo user bus")
    count = desktop.get_child_count()
    if count < 0 or count > 64:
        raise RuntimeError(f"AT-SPI desktop application count outside bound: {count}")
    matches = []
    for index in range(count):
        app = desktop.get_child_at_index(index)
        if app is not None and app.get_process_id() == pid:
            matches.append(app)
    if len(matches) > 1:
        raise RuntimeError(f"AT-SPI exposes multiple applications for fixture PID {pid}")
    return matches[0] if matches else None


def snapshot(atspi, app) -> list[dict]:
    """Query a bounded subtree of the exact-PID app; never collect other apps' text."""
    roles = {atspi.Role.ENTRY: "entry", atspi.Role.TEXT: "text",
             atspi.Role.PUSH_BUTTON: "push button"}
    nodes: list[dict] = []
    stack = [(app, (), 0)]
    text_total = 0
    while stack:
        accessible, path, depth = stack.pop()
        if depth > MAX_DEPTH or len(nodes) >= MAX_NODES:
            raise RuntimeError("fixture AT-SPI tree exceeds 12 levels or 160 nodes")
        state = accessible.get_state_set()
        role = accessible.get_role()
        name = accessible.get_name() or ""
        if len(name) > 256:
            raise RuntimeError("fixture AT-SPI accessible name exceeds 256 characters")
        row = {
            "path": path, "name": name, "role": roles.get(role, accessible.get_role_name()),
            "focused": bool(state.contains(atspi.StateType.FOCUSED)),
            "showing": bool(state.contains(atspi.StateType.SHOWING)),
            "editable": bool(state.contains(atspi.StateType.EDITABLE)),
            "text": None, "characters": None,
        }
        if accessible.is_text():
            iface = accessible.get_text_iface()
            if iface is None:
                raise RuntimeError(f"AT-SPI Text advertised without interface at {path}")
            chars = iface.get_character_count()
            if chars < 0 or chars > MAX_TEXT or text_total + chars > 20000:
                raise RuntimeError(f"fixture AT-SPI Text exceeds 8192 per node/20000 total at {path}")
            row["characters"] = chars
            row["text"] = iface.get_text(0, chars)
            text_total += chars
        nodes.append(row)
        count = accessible.get_child_count()
        if count < 0 or count > MAX_NODES or count + len(nodes) > MAX_NODES:
            raise RuntimeError(f"fixture AT-SPI child count exceeds bound at {path}: {count}")
        for index in reversed(range(count)):
            child = accessible.get_child_at_index(index)
            if child is None:
                raise RuntimeError(f"AT-SPI child {path + (index,)} disappeared")
            stack.append((child, path + (index,), depth + 1))
    return nodes


def one(nodes: list[dict], name: str, role: str | None = None,
        *, focus_preferred: bool = False) -> dict:
    found = [n for n in nodes if n["name"] == name and n["showing"]
             and (role is None or n["role"] == role)]
    if focus_preferred and len(found) > 1:
        focused = [n for n in found if focused_within(nodes, n)]
        if focused:
            deepest = max(len(n["path"]) for n in focused)
            best = [n for n in focused if len(n["path"]) == deepest]
            if len(best) == 1:
                return best[0]
    if len(found) != 1:
        raise RuntimeError(f"expected one showing {name!r} {role or ''}; found {len(found)}")
    return found[0]


def focused_within(nodes: list[dict], target: dict) -> bool:
    return any(node["showing"] and node["focused"]
               and node["path"][:len(target["path"])] == target["path"]
               for node in nodes)


def observe(stage: str, nodes: list[dict], approval_responses: int) -> dict:
    obs = {"nodes": len(nodes), "approval_responses": approval_responses}
    if stage in ("prompt", "unsolicited", "panel_escape", "escape"):
        entry = one(nodes, "Ask Exo, or type an app name", "entry")
        obs.update(entry_name=entry["name"], entry_role=entry["role"],
                   entry_focused=focused_within(nodes, entry))
    if stage == "unsolicited":
        panel = one(nodes, "Panels")
        obs.update(panel_showing=panel["showing"], panel_focused=focused_within(nodes, panel),
                   panel_content_present=any(n["showing"] and "AT-SPI panel text" in
                                             ((n["text"] or "") + n["name"]) for n in nodes))
    elif stage == "panels":
        panel = one(nodes, "Panels")
        close = one(nodes, "Close Synthetic choices panel", "push button")
        obs.update(panel_name=panel["name"], panel_focused=focused_within(nodes, panel),
                   panel_content_present=any(n["showing"] and "AT-SPI panel text" in
                                             ((n["text"] or "") + n["name"]) for n in nodes),
                   close_name=close["name"], close_role=close["role"],
                   close_focused=focused_within(nodes, close))
    elif stage == "transcript":
        view = one(nodes, "Recent conversation, loaded lines only", "text")
        obs.update(view_name=view["name"], view_role=view["role"],
                   view_focused=focused_within(nodes, view), view_editable=view["editable"],
                   marker_present=MARKER in (view["text"] or ""))
    elif stage == "approval":
        summary = one(nodes, SUMMARY, focus_preferred=True)
        detail = one(nodes, "Full request details", "text")
        yes = one(nodes, "Yes", "push button")
        no = one(nodes, "No", "push button")
        obs.update(summary_name=summary["name"], summary_role=summary["role"],
                   summary_text_present=SUMMARY in (summary["text"] or ""),
                   summary_focused=focused_within(nodes, summary),
                   yes_name=yes["name"], yes_role=yes["role"], yes_focused=focused_within(nodes, yes),
                   no_name=no["name"], no_role=no["role"], no_focused=focused_within(nodes, no),
                   detail_name=detail["name"], detail_role=detail["role"],
                   detail_editable=detail["editable"], detail_characters=detail["characters"],
                   suffix_present=SUFFIX in (detail["text"] or ""))
    return obs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-acceptance", action="store_true")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--image-source", required=True)
    parser.add_argument("--diagnostic-source", required=True)
    args = parser.parse_args()
    try:
        require_vm(opt_in=args.vm_acceptance)
        protected_fixture(args.fixture)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    import gi  # no accessibility or display connection before the VM and file guards

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    Atspi.set_timeout(800, 3000)  # official libatspi millisecond method/startup bounds
    Atspi.init()
    fixture = None
    stage = "initialization"
    last_tree: list[dict] = []
    try:
        fixture = Fixture(args.fixture)
        fixture.read("ready")
        checks = []
        for stage in STAGES:
            if stage == "unsolicited":
                fixture.command("unsolicited")  # synthetic event, not keyboard input
            elif stage == "panels":
                type_key("panels")
                type_key("-k", "Return")
            elif stage == "panel_escape":
                type_key("-k", "Escape")
            elif stage == "transcript":
                type_key("transcript")
                type_key("-k", "Return")
            elif stage == "approval":
                fixture.command("approval")  # synthetic event, not keyboard input
            elif stage == "escape":
                type_key("-k", "Escape")
            deadline = time.monotonic() + STAGE_SECONDS
            last_error = "AT-SPI state did not settle"
            while time.monotonic() < deadline:
                app = app_for_pid(Atspi, fixture.proc.pid)
                if app is None:
                    last_error = f"no AT-SPI application with exact fixture PID {fixture.proc.pid}"
                else:
                    try:
                        last_tree = snapshot(Atspi, app)
                        ack = fixture.command("status")
                        obs = observe(stage, last_tree, ack["approval_responses"])
                        if stage_valid(stage, obs):
                            checks.append({"stage": stage, "status": "PASS", "observed": obs})
                            break
                        last_error = f"{stage} AT-SPI values failed contract: {obs!r}"
                    except RuntimeError as exc:
                        last_error = str(exc)
                time.sleep(0.1)
            else:
                raise RuntimeError(last_error)
        return_data = {"probe": PROBE, "status": "PASS", "backend": "wayland-layer",
                       "image_source": args.image_source, "diagnostic_source": args.diagnostic_source,
                       "fixture_pid": fixture.proc.pid, "checks": checks}
        output = json.dumps(return_data, sort_keys=True)
        if len(output.encode("utf-8")) > 128 * 1024:
            raise RuntimeError("PASS evidence exceeds 128 KiB")
        print(output, flush=True)
        return 0
    except Exception as exc:
        tree = [{"path": n["path"], "name": n["name"][:96], "role": n["role"],
                 "focused": n["focused"], "showing": n["showing"]} for n in last_tree[:MAX_NODES]]
        failure = {"probe": PROBE, "status": "FAIL", "stage": stage,
                   "error": f"{type(exc).__name__}: {exc}"[:500],
                   "fixture_stderr": fixture.stderr_excerpt() if fixture else "",
                   "image_source": args.image_source,
                   "diagnostic_source": args.diagnostic_source,
                   "fixture_pid": fixture.proc.pid if fixture else None,
                   "observed": tree}
        output = json.dumps(failure, sort_keys=True)
        if len(output.encode("utf-8")) > 128 * 1024:
            failure["observed"] = []
            failure["error"] = "AT-SPI evidence exceeded 128 KiB"
            output = json.dumps(failure, sort_keys=True)
        print(output, flush=True)
        return 1
    finally:
        if fixture is not None:
            fixture.close()


if __name__ == "__main__":
    raise SystemExit(main())

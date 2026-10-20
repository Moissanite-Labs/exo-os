"""Which GSK renderer the production surface uses (OS-20, issue #107).

The launcher asks a child process whether GTK's default (GL) renderer works on this display. If it does, the
surface runs with that default untouched. If the child fails, hangs, crashes or reports a software renderer,
the surface is started with GSK_RENDERER=cairo. Either way the choice is logged on one line:

    exo-surface: renderer=<gl|cairo|default> source=<environment|probe-ok|probe-failed|no-display> detail=...

An explicit GSK_RENDERER in the environment is the operator's choice and is never probed or changed. The
`vmtest` image sets it to cairo because its virtual machine has no GPU.

The probe finds a renderer that is missing, fails to start, crashes or hangs. It cannot see glyphs that draw
blank on a renderer that starts fine (the failure behind #52). That case is not covered.

Run `python -m exo_os.surface.renderer` to print the choice as JSON without starting the surface. This module
imports no GTK; only the child process does.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import asdict, dataclass

GL_RENDERERS = frozenset({"GskGLRenderer", "GskNglRenderer", "GskVulkanRenderer"})
TIMEOUT = 10.0
LOG_PREFIX = "exo-surface: renderer="
LOG_LINE = re.compile(r"exo-surface: renderer=(gl|cairo|default) source=(environment|probe-ok|probe-failed|no-display)"
                      r"(?: detail=(.*))?\Z")

PROBE_CODE = """\
import sys
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
if not Gtk.init_check():
    sys.exit(3)
window = Gtk.Window()
window.realize()
renderer = window.get_renderer()
if renderer is None:
    sys.exit(4)
print(type(renderer).__name__)
"""


@dataclass(frozen=True)
class Choice:
    renderer: str  # "gl" (GTK's default, GL), "cairo", or "default" (left to GTK, not probed)
    source: str
    detail: str = ""

    def log_line(self) -> str:
        tail = f" detail={self.detail}" if self.detail else ""
        return f"exo-surface: renderer={self.renderer} source={self.source}{tail}"


Probe = Callable[[Mapping[str, str]], tuple[bool, str]]


def clean(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:=, -]", "?", text)[:120]


def run_probe(env: Mapping[str, str]) -> tuple[bool, str]:
    """Start GTK in a child with the default renderer. Returns (gl works, short reason)."""
    child = {k: v for k, v in env.items() if k != "GSK_RENDERER"}
    try:
        done = subprocess.run([sys.executable, "-c", PROBE_CODE], env=child, capture_output=True, text=True,
                              timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {TIMEOUT:g}s"
    except OSError as err:
        return False, clean(f"probe did not start: {type(err).__name__}")
    if done.returncode != 0:
        return False, f"probe exited {done.returncode}"
    name = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    if name in GL_RENDERERS:
        return True, name
    return False, clean(f"default renderer is {name or 'unknown'}")


def choose(env: Mapping[str, str], probe: Probe = run_probe) -> Choice:
    if env.get("GSK_RENDERER"):
        return Choice("default", "environment", clean(f"GSK_RENDERER={env['GSK_RENDERER']}"))
    if env.get("GDK_BACKEND", "wayland").split(",")[0] not in ("wayland", "*") or not env.get("WAYLAND_DISPLAY"):
        return Choice("default", "no-display", "no wayland display to probe")
    try:
        ok, detail = probe(env)
    except Exception as err:  # noqa: BLE001 - a broken probe must fall back, never stop the surface
        ok, detail = False, clean(f"probe error: {type(err).__name__}")
    return Choice("gl", "probe-ok", detail) if ok else Choice("cairo", "probe-failed", detail)


def apply(env: MutableMapping[str, str], probe: Probe = run_probe, log: Callable[[str], None] | None = None) -> Choice:
    """Choose, set GSK_RENDERER=cairo when the probe failed, and log the choice. Call before GTK starts."""
    choice = choose(env, probe)
    if choice.renderer == "cairo":
        env["GSK_RENDERER"] = "cairo"
    (log or (lambda line: print(line, file=sys.stderr, flush=True)))(choice.log_line())
    return choice


def parse_log_line(line: str) -> dict:
    """Validate one launcher log line; raises ValueError unless it is exactly the documented shape."""
    match = LOG_LINE.fullmatch(line.strip())
    if not match:
        raise ValueError("not a surface renderer log line")
    renderer, source, detail = match.groups()
    if (renderer, source) not in {("default", "environment"), ("default", "no-display"), ("gl", "probe-ok"),
                                  ("cairo", "probe-failed")}:
        raise ValueError("renderer and source disagree")
    return {"renderer": renderer, "source": source, "detail": detail or ""}


def main() -> int:
    print(json.dumps(asdict(choose(os.environ))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

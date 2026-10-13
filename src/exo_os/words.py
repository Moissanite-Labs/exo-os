"""Plain words for changes, used in history, in the ledger and on screen."""
from __future__ import annotations

import json
import unicodedata

from typing import Any

LAYOUT_NAMES = {"us": "English", "ara": "Arabic", "pk": "Urdu", "gb": "English (UK)", "fr": "French",
                "de": "German", "es": "Spanish", "in": "Hindi", "ir": "Persian", "tr": "Turkish"}

LABELS = {
    "display.scale": "Screen size",
    "display.text_scale": "Text size in apps",
    "keyboard.layouts": "Keyboard languages",
    "keyboard.switch": "Keyboard language switch",
    "keyboard.repeat_rate": "Key repeat speed",
    "keyboard.repeat_delay": "Key repeat delay",
    "pointer.natural_scroll": "Natural scrolling",
    "pointer.sensitivity": "Pointer speed",
    "idle.dim_seconds": "Dim the screen after",
    "idle.lock_seconds": "Lock the screen after",
    "idle.screen_off_seconds": "Turn the screen off after",
    "look.theme": "Theme",
    "look.gaps": "Space between windows",
    "look.border": "Window border",
    "surface.key": "Key for the prompt bar",
    "surface.language": "Language of the prompt bar",
    "surface.plain": "Plain mode",
}


def value_words(path: str, value: Any) -> str:
    if path == "keyboard.layouts":
        return ", ".join(LAYOUT_NAMES.get(v, v) for v in value)
    if path.startswith("idle."):
        if value == 0:
            return "never"
        if value % 3600 == 0:
            return f"{value // 3600} hour" + ("s" if value != 3600 else "")
        if value % 60 == 0:
            return f"{value // 60} minute" + ("s" if value != 60 else "")
        return f"{value} seconds"
    if isinstance(value, bool):
        return "on" if value else "off"
    if path in ("display.scale", "display.text_scale"):
        return f"{round(value * 100)}%"
    return str(value)


def set_summary(path: str, value: Any) -> str:
    return f"{LABELS.get(path, path)}: {value_words(path, value)}"


def _q(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def describe_call(name: str, args: dict[str, Any] | None) -> str | None:
    """One plain question for an approval card, or None when this tool has no wording here.

    Exo's gate words an approval for an operator ("system_package_install (costly, private). Why: ...").
    Exo's pre_approval_request hook is declared but not called by the core (aska-digital/exo#26), so the surface
    words the card itself, for exo-os tools and the common Exo ones."""
    a = args or {}
    if name == "system_package_install":
        names = ", ".join(a.get("names") or []) or "software"
        reason = f" Reason: {_q(a['reason'])}." if a.get("reason") else ""
        return f"Install {names} on this computer?{reason} It can be undone."
    if name == "system_snapshot_undo":
        return f"Put the system back as it was before snapshot {a.get('post')}? Software added then will be removed."
    if name == "machine_set" and a.get("setting") in LABELS:
        return f"Change {LABELS[a['setting']].lower()} to {value_words(a['setting'], a.get('value'))}?"
    if name == "machine_undo":
        return "Undo the last change to this computer?"
    if name in ("machine_app_put", "machine_app_unbind"):
        verb = "Add" if name == "machine_app_put" else "Remove"
        return f"{verb} {a.get('name', 'this app')}?"
    if name == "app_open":
        return f"Open {a.get('name', 'this app')}?"
    if name == "send_message":
        target = a.get("target") or a.get("chat_id") or "this chat"
        return f"Send this message to {target}? “{_q(a.get('message') or a.get('text') or '')}”"
    if name in ("bash", "terminal", "shell", "run_command"):
        return f"Run this on the computer? {_q(a.get('command') or a.get('cmd') or '', 100)}"
    if name in ("write_file", "patch", "edit_file"):
        path = str(a.get("path") or a.get("file_path") or "a file")
        return f"Change the file {path.rsplit('/', 1)[-1]}?"
    return None


def credential_request_preview(args: dict[str, Any], approval_id: str) -> tuple[str, str, bool]:
    """Full literal request metadata, with its bound destination checked against operator policy.

    This reads only the root-owned policy through the egress policy API. It never imports the broker
    or reads a credential file. JSON escaping makes controls and markup literal and inspectable.
    """
    request = {"body": "", **args}
    literal = json.dumps({"approval_request_id": approval_id, "request": request}, indent=2, ensure_ascii=False)
    # Keep normal Unicode readable; expose direction/format controls and Unicode line separators as
    # JSON escapes so a payload cannot visually overwrite its destination or hide a suffix.
    literal = "".join(json.dumps(c, ensure_ascii=True)[1:-1] if (
        unicodedata.category(c) in {"Cf", "Zl", "Zp"} or 0x7F <= ord(c) < 0xA0
    ) else c for c in literal)
    details = "Outbound request (full JSON; escaped characters preserve exact text):\n" + literal
    blocked = "This request cannot be approved. "
    allowed_fields = {"profile", "request_id", "expected_origin", "method", "path", "body"}
    required = allowed_fields - {"body"}
    if not required <= set(args) or set(args) - allowed_fields:
        return blocked + "The request is incomplete or invalid.", details, False
    try:
        from .egress.policy import load_policy, validate_request
        from .sandbox.policy import SandboxError
    except ImportError:
        return blocked + "Destination policy is unavailable.", details, False
    try:
        validate_request({"version": 1, "nonce": "surface-preview", **request})
    except (ValueError, TypeError, UnicodeError):
        return blocked + "The request is incomplete or invalid.", details, False
    try:
        profile = load_policy().get(args["profile"])
    except (OSError, ValueError, SandboxError):
        return blocked + "Destination policy is unavailable.", details, False
    if profile is None:
        return blocked + "The destination profile is not configured.", details, False
    if profile.origin != args["expected_origin"]:
        return blocked + "The destination does not match operator policy.", details, False
    if not profile.permits(args["method"], args["path"]):
        return blocked + "This method or path is not allowed by operator policy.", details, False
    return f"Send this HTTPS request to {profile.origin} using your saved credential?", details, True


def plain_summary(summary: str) -> str:
    """Exo's gate summary without the operator parts: '<tool> (costly, private). Why: X. Default if no
    reply: skip.' becomes 'X.'"""
    import re

    text = re.sub(r"^[\w.:-]+ \((?:reversible|costly|irreversible)[^)]*\)\.\s*", "", summary.strip())
    text = re.sub(r"\s*Default if no reply:.*$", "", text)
    text = re.sub(r"^Why:\s*", "", text)
    text = text[:1].upper() + text[1:] if text else "Exo is waiting for your answer."
    return text if text.endswith((".", "?", "!")) else text + "."

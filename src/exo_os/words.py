"""Plain words for changes, used in history, in the ledger and on screen."""
from __future__ import annotations

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


def plain_summary(summary: str) -> str:
    """Exo's gate summary without the operator parts: '<tool> (costly, private). Why: X. Default if no
    reply: skip.' becomes 'X.'"""
    import re

    text = re.sub(r"^[\w.:-]+ \((?:reversible|costly|irreversible)[^)]*\)\.\s*", "", summary.strip())
    text = re.sub(r"\s*Default if no reply:.*$", "", text)
    text = re.sub(r"^Why:\s*", "", text)
    text = text[:1].upper() + text[1:] if text else "Exo is waiting for your answer."
    return text if text.endswith((".", "?", "!")) else text + "."

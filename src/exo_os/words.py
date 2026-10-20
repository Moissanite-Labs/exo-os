"""Plain words for changes, used in history, in the ledger and on screen."""
from __future__ import annotations

import json
import math
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


class _Malformed(Exception):
    """The model's arguments cannot be worded truthfully."""


def _text(a: dict[str, Any], *keys: str, default: str = "") -> str:
    """The first of `keys` present in `a` as a string. A present value of any other type is malformed."""
    for key in keys:
        value = a.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise _Malformed(key)
        return value
    return default


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _setting_words(setting: str, value: Any) -> str:
    """value_words for model-supplied input: only the shapes it can word truthfully."""
    if setting == "keyboard.layouts":
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            raise _Malformed(setting)
    elif setting.startswith("idle."):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _Malformed(setting)
    elif setting in ("display.scale", "display.text_scale"):
        if not _number(value) or value <= 0:
            raise _Malformed(setting)
    elif not isinstance(value, (bool, str)) and not _number(value):
        raise _Malformed(setting)
    return value_words(setting, value)


# Every install runs a full system upgrade first (sysd package_install: pacman -Sy, then -Su). Every
# place that describes package.install to a person says so, in these words.
INSTALL_UPGRADE_NOTE = "This refreshes the package lists and upgrades all system packages first."


def _describe(name: str, args: dict[str, Any] | None) -> str | None:
    a = {} if args is None else args
    if not isinstance(a, dict):
        raise _Malformed("arguments")
    if name == "system_package_install":
        names = a.get("names")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
            raise _Malformed("names")  # a bare string would otherwise read as one letter per package
        reason = f" Reason: {_q(_text(a, 'reason'))}." if _text(a, "reason") else ""
        return f"Install {', '.join(names)} on this computer?{reason} {INSTALL_UPGRADE_NOTE} It can be undone."
    if name == "system_snapshot_undo":
        post = a.get("post")
        if not isinstance(post, str) or not post.strip():
            raise _Malformed("post")
        return f"Put the system back as it was before snapshot {post}? Software added then will be removed."
    if name == "machine_set":
        setting = a.get("setting")
        if setting is not None and not isinstance(setting, str):
            raise _Malformed("setting")
        if setting not in LABELS:
            return None  # not a setting this wording knows; the plain summary stands
        return f"Change {LABELS[setting].lower()} to {_setting_words(setting, a.get('value'))}?"
    if name == "machine_undo":
        return "Undo the last change to this computer?"
    if name in ("machine_app_put", "machine_app_unbind"):
        verb = "Add" if name == "machine_app_put" else "Remove"
        return f"{verb} {_text(a, 'name', default='this app')}?"
    if name == "app_open":
        return f"Open {_text(a, 'name', default='this app')}?"
    if name == "send_message":
        target = _text(a, "target", "chat_id", default="this chat")
        return f"Send this message to {target}? “{_q(_text(a, 'message', 'text'))}”"
    if name in ("bash", "terminal", "shell", "run_command"):
        return f"Run this on the computer? {_q(_text(a, 'command', 'cmd'), 100)}"
    if name in ("write_file", "patch", "edit_file"):
        path = _text(a, "path", "file_path", default="a file")
        return f"Change the file {path.rsplit('/', 1)[-1]}?"
    return None


def plain_wording(name: Any, args: Any) -> tuple[str | None, bool]:
    """(plain question, malformed). Never raises, whatever shape the model's arguments have.

    `malformed` is true when this is a tool the wording knows but its arguments cannot be worded
    truthfully (or the wording failed for any reason). The caller falls back to the plain summary
    and should offer nothing but refusal."""
    try:
        return _describe(name, args), False
    except Exception:  # _Malformed, TypeError, ValueError, OverflowError, anything: the card must still appear
        return None, True


def describe_call(name: str, args: dict[str, Any] | None) -> str | None:
    """One plain question for an approval card, or None when this tool has no wording here or its
    arguments are malformed. Never raises.

    Exo's gate words an approval for an operator ("system_package_install (costly, private). Why: ...").
    Exo's pre_approval_request hook is declared but not called by the core (aska-digital/exo#26), so the surface
    words the card itself, for exo-os tools and the common Exo ones. Exo asks before it validates arguments, so
    these are the model's raw arguments: every one is type-checked before it is put in a sentence."""
    return plain_wording(name, args)[0]


def credential_request_preview(args: dict[str, Any], approval_id: str) -> tuple[str, str, bool]:
    """Full literal request metadata, with its bound destination checked against operator policy.

    This reads only the root-owned policy through the egress policy API. It never imports the broker
    or reads a credential file. JSON escaping makes controls and markup literal and inspectable.
    """
    # Put the destination and operation ahead of even a maximum-sized body.
    # Unknown metadata remains visible and is still rejected by validation below.
    metadata = ("profile", "request_id", "expected_origin", "method", "path")
    request = {key: args[key] for key in metadata if key in args}
    request.update((key, value) for key, value in args.items() if key not in metadata and key != "body")
    request["body"] = args.get("body", "")
    literal = json.dumps({"approval_request_id": approval_id, "request": request}, indent=2, ensure_ascii=False)
    # Keep normal Unicode readable; expose direction/format controls and Unicode line separators as
    # JSON escapes so a payload cannot visually overwrite its destination or hide a suffix.
    literal = _escape_format_controls(literal)
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
    return f"Send {args['method']} {args['path']} to {profile.origin} using your saved credential?", details, True


def approval_request_details(name: str, args: dict[str, Any], approval_id: str) -> str:
    """An exact, selectable ordinary approval preview in both surface modes.

    JSON makes model-provided markup literal. Escaping invisible direction and
    format controls keeps the destination and trailing text inspectable.
    """
    literal = json.dumps({"approval_request_id": approval_id, "tool": name, "arguments": args},
                         indent=2, ensure_ascii=False)
    return "Full request details (literal JSON; escaped controls preserve exact text):\n" + _escape_format_controls(literal)


def _escape_format_controls(text: str) -> str:
    return "".join(json.dumps(c, ensure_ascii=True)[1:-1] if (
        unicodedata.category(c) in {"Cf", "Zl", "Zp"} or 0x7F <= ord(c) < 0xA0
    ) else c for c in text)


def plain_summary(summary: str) -> str:
    """Exo's gate summary without the operator parts: '<tool> (costly, private). Why: X. Default if no
    reply: skip.' becomes 'X.' A ' Link: ...' segment between the reason and the default is kept."""
    import re

    text = re.sub(r"^[\w.:-]+ \((?:reversible|costly|irreversible)[^)]*\)\.\s*", "", summary.strip())
    text = re.sub(r"\s*Default if no reply:.*$", "", text)
    text = re.sub(r"^Why:\s*", "", text)
    text = text[:1].upper() + text[1:] if text else "Exo is waiting for your answer."
    return text if text.endswith((".", "?", "!")) else text + "."

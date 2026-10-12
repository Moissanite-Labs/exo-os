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

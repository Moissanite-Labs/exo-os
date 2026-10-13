"""Fixed, bounded evidence contract for the disposable surface AT-SPI diagnostic."""

from __future__ import annotations

import json
import re
from typing import Any

PROBE = "exo-surface-atspi-v1"
STAGES = ("prompt", "unsolicited", "panels", "panel_escape", "transcript", "approval", "escape")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MARKER = "AT_SPI_TRANSCRIPT_MARKER"
SUFFIX = "AT_SPI_APPROVAL_SUFFIX"
SUMMARY = "Synthetic approval, read before consenting"


def validate(data: Any, *, image_source: str, diagnostic_source: str, exit_code: int) -> bool:
    if exit_code != 0 or not isinstance(data, dict):
        return False
    if set(data) != {"probe", "status", "backend", "image_source", "diagnostic_source",
                     "fixture_pid", "checks", "gsk_renderer"}:
        return False
    if (data["probe"] != PROBE or data["status"] != "PASS" or data["backend"] != "wayland-layer"
            or data["image_source"] != image_source or data["diagnostic_source"] != diagnostic_source
            or data["gsk_renderer"] != "cairo"
            or not SHA.fullmatch(image_source) or not SHA.fullmatch(diagnostic_source)
            or type(data["fixture_pid"]) is not int or data["fixture_pid"] <= 1):
        return False
    checks = data["checks"]
    if not isinstance(checks, list) or [c.get("stage") for c in checks if isinstance(c, dict)] != list(STAGES):
        return False
    if len(checks) != len(STAGES):
        return False
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"stage", "status", "observed"}:
            return False
        if check["status"] != "PASS" or not isinstance(check["observed"], dict):
            return False
        obs = check["observed"]
        if (type(obs.get("nodes")) is not int or not 1 <= obs["nodes"] <= 160
                or type(obs.get("approval_responses")) is not int or obs["approval_responses"] != 0):
            return False
        if not stage_valid(check["stage"], obs):
            return False
    return True


def stage_valid(stage: str, obs: dict) -> bool:
    if (type(obs.get("nodes")) is not int or not 1 <= obs["nodes"] <= 160
            or obs.get("renderer") != "GskCairoRenderer"
            or type(obs.get("approval_responses")) is not int or obs["approval_responses"] != 0):
        return False
    if stage in ("prompt", "panel_escape", "escape"):
        return (obs.get("entry_name") == "Ask Exo, or type an app name"
                and obs.get("entry_role") == "entry" and obs.get("entry_focused") is True)
    if stage == "unsolicited":
        return (obs.get("entry_name") == "Ask Exo, or type an app name"
                and obs.get("entry_role") == "entry" and obs.get("entry_focused") is True
                and obs.get("panel_showing") is True and obs.get("panel_focused") is False
                and obs.get("panel_content_present") is True)
    if stage == "panels":
        return (obs.get("panel_name") == "Panels" and obs.get("panel_focused") is True
                and obs.get("panel_content_present") is True
                and obs.get("close_name") == "Close Synthetic choices panel"
                and obs.get("close_role") == "push button"
                and obs.get("close_focused") is False)
    if stage == "transcript":
        return (obs.get("view_name") == "Recent conversation, loaded lines only"
                and obs.get("view_role") == "text" and obs.get("view_focused") is True
                and obs.get("view_editable") is False and obs.get("marker_present") is True)
    if stage == "approval":
        return (obs.get("summary_name") == SUMMARY and obs.get("summary_focused") is True
                and obs.get("summary_role") != "push button"
                and obs.get("summary_text_present") is True
                and obs.get("yes_role") == "push button" and obs.get("no_role") == "push button"
                and obs.get("yes_name") == "Yes" and obs.get("no_name") == "No"
                and obs.get("yes_focused") is False and obs.get("no_focused") is False
                and obs.get("detail_name") == "Full request details"
                and obs.get("detail_role") == "text" and obs.get("detail_editable") is False
                and obs.get("suffix_present") is True
                and type(obs.get("detail_characters")) is int
                and 2048 <= obs["detail_characters"] <= 8192)
    return False


def parse_and_validate(raw: str, *, image_source: str, diagnostic_source: str,
                       exit_code: int) -> bool:
    if len(raw.encode("utf-8")) > 128 * 1024:
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        return False
    return validate(data, image_source=image_source, diagnostic_source=diagnostic_source,
                    exit_code=exit_code)

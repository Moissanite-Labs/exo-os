"""Coverage criteria for native keyboard regressions, independent of GTK imports."""
from __future__ import annotations

PROMPT_MARKER = "exo focus marker"
PROMPT_STAGES = ("unsolicited-panels", "panels-close", "updated-panels-close", "unsolicited-panel-update",
                 "approval-defer", "external-work-return", "generic-answer-return")


def native_prompt_delivered(record: object) -> bool:
    """A local focus target alone cannot prove the compositor delivered keys."""
    keys = {"stage", "text", "model_text", "events", "sent_delta", "review_unchanged", "reviewed", "entry_target",
            "mapped", "gtk_active", "panel_update_injected"}
    if (not isinstance(record, dict) or set(record) != keys or record["stage"] not in PROMPT_STAGES
            or record["text"] != PROMPT_MARKER or record["model_text"] != PROMPT_MARKER
            or type(record["sent_delta"]) is not int or record["sent_delta"] != 0
            or record["reviewed"] is not False
            or any(record[key] is not True for key in ("review_unchanged", "entry_target", "mapped"))
            or type(record["gtk_active"]) is not bool
            or record["panel_update_injected"] is not (record["stage"] == "unsolicited-panel-update")):
        return False
    events = record["events"]
    return (isinstance(events, list) and len(events) == len(PROMPT_MARKER)
            and all(isinstance(row, dict) and set(row) == {"keyval", "code"}
                    and type(row["keyval"]) is int and row["keyval"] == ord(char)
                    and type(row["code"]) is int and row["code"] > 0
                    for row, char in zip(events, PROMPT_MARKER)))


def native_external_panel_delivered(record: object) -> bool:
    """A panel update must leave actual typing in the other work window."""
    keys = {"mode", "text", "events", "panel_update_injected", "sent_delta", "reviewed",
            "external_active", "external_target", "prompt_unchanged"}
    if (not isinstance(record, dict) or set(record) != keys or record["mode"] != "native"
            or record["text"] != "yesterday" or record["reviewed"] is not False
            or type(record["sent_delta"]) is not int or record["sent_delta"] != 0
            or any(record[key] is not True for key in ("panel_update_injected", "external_active",
                                                     "external_target", "prompt_unchanged"))):
        return False
    events = record["events"]
    return (isinstance(events, list) and len(events) == len("yesterday")
            and all(isinstance(row, dict) and set(row) == {"keyval", "code"}
                    and type(row["keyval"]) is int and row["keyval"] == ord(char)
                    and type(row["code"]) is int and row["code"] > 0
                    for row, char in zip(events, "yesterday")))


def native_repeat_delivered(events: object, *, released: bool) -> bool:
    if not isinstance(events, list) or not 1 <= len(events) <= 128 or released is not True:
        return False
    keys = {"pending_id", "reviewed_id", "code", "held_before", "blocked", "accepted"}
    if any(not isinstance(row, dict) or set(row) != keys or type(row["code"]) is not int
           or any(type(row[key]) is not bool for key in ("held_before", "blocked", "accepted"))
           for row in events):
        return False
    initial = next(((index, row) for index, row in enumerate(events) if row["pending_id"] == "held_f6_a"
                    and row["reviewed_id"] is None and row["code"] > 0
                    and not row["held_before"] and not row["blocked"] and row["accepted"]), None)
    if initial is None:
        return False
    index, first = initial
    return any(row["pending_id"] == "held_f6_b" and row["reviewed_id"] is None
               and row["code"] == first["code"] and row["held_before"]
               and not row["blocked"] and not row["accepted"] for row in events[index + 1:])

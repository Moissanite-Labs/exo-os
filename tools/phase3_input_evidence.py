"""Coverage criteria for the native held-F6 regression, independent of GTK imports."""
from __future__ import annotations


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

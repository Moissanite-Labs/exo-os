"""Plain read-only spending summaries. Never turn missing accounting into zero."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

UNAVAILABLE = "Model spending information is unavailable. Ask an administrator to check the accounting."


def _usd(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,9})?", value):
        raise ValueError("invalid recorded amount")
    number = Decimal(value)
    whole, _, fraction = format(number, "f").partition(".")
    return "$" + whole + "." + fraction.rstrip("0").ljust(2, "0")


def describe_spend(value: Any) -> str:
    if not isinstance(value, dict):
        return UNAVAILABLE
    state = value.get("state")
    if not isinstance(state, str) or state not in {"offline", "ready", "stopped"}:  # a list is unhashable
        return UNAVAILABLE
    try:
        daily, monthly, day, month, pending = (_usd(value[key]) for key in
            ("daily_cap_usd", "monthly_cap_usd", "day_charged_usd", "month_charged_usd", "pending_reserved_usd"))
        count = value["pending_count"]
        if type(count) is not int or not 0 <= count <= 2**31:
            raise ValueError("invalid pending count")
    except (KeyError, ValueError):
        return UNAVAILABLE
    lines = [{"offline": "Paid models are disabled.", "ready": "Model spending limits are active.",
              "stopped": "Exo's model work is stopped."}[value["state"]],
             f"Daily limit: {daily}. Monthly limit: {monthly}.",
             f"Recorded charges: {day} today; {month} this month (UTC)."]
    if count:
        lines.append(f"Unsettled calls: {count}, with {pending} still reserved against the limits.")
    if value["state"] == "stopped":
        lines.append("Ask an administrator to review the accounting before resuming. Approval replies do not raise these limits.")
    return "\n".join(lines)

"""Safe host desktop operations used by the compulsory executor.

The compositor runs outside tool namespaces. Generated app definitions therefore
cannot contain executables or argument lists chosen by a model.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from .policy import SandboxError

COMMANDS = {"chromium": "/usr/bin/chromium", "foot": "/usr/bin/foot"}


def required() -> bool:
    return os.environ.get("EXO_TOOL_EXECUTOR_REQUIRED") == "1"


def check_app(app: dict) -> None:
    url = app.get("url")
    if url:
        if not isinstance(url, str):
            raise SandboxError("host web app address must be text")
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None or
                parts.password is not None or any(ord(char) < 33 or ord(char) == 127 for char in url)):
            raise SandboxError("host web apps require an HTTPS address without credentials or control characters")
        return
    try:
        known = app.get("command") in COMMANDS
    except TypeError:
        known = False
    if not known:
        raise SandboxError("this app command has no reviewed host launch path; use the isolated workspace")


def check_profile(profile) -> None:
    for app in profile.data["apps"]:
        check_app(app)

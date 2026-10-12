"""The machine profile: one small TOML file that says how this person's machine should be.

Everything a person might ask to change about their desktop lives here: text size, keyboard languages,
idle times, which apps sit on which keys. The real config files (Hyprland Lua, hypridle) are rendered
from it (exo_os.render), so a change is always a diff of this file plus the diff it causes.

The file is plain data with a fixed shape. `set_value` and the app helpers are the only ways the agent
changes it, so every change is a named field with a checked value.
"""
from __future__ import annotations

import copy
import re
import tomllib
from dataclasses import dataclass
from typing import Any

DEFAULT: dict[str, Any] = {
    "display": {"scale": 1.0, "text_scale": 1.0},
    "keyboard": {"layouts": ["us"], "switch": "grp:alts_toggle", "repeat_rate": 25, "repeat_delay": 600},
    "pointer": {"natural_scroll": False, "sensitivity": 0.0},
    "idle": {"dim_seconds": 600, "lock_seconds": 1800, "screen_off_seconds": 0},
    "look": {"theme": "light", "gaps": 0, "border": 1},
    "surface": {"key": "SUPER+SPACE", "language": "en", "plain": True},
    "apps": [
        {"name": "Browser", "command": "chromium", "class": "chromium", "key": "SUPER+B", "workspace": 1},
        {"name": "Terminal", "command": "foot", "class": "foot", "key": "SUPER+RETURN"},
    ],
}

KEY_RE = re.compile(r"^(SUPER|CTRL|ALT|SHIFT)(\+(SUPER|CTRL|ALT|SHIFT))*\+[A-Z0-9]+$|^[A-Z0-9]+$")
LAYOUT_RE = re.compile(r"^[a-z]{2,3}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")

# Field -> (type, check). Only these fields can be set. Each check returns an error string or "".
_RANGE = lambda lo, hi: (lambda v: "" if lo <= v <= hi else f"must be between {lo} and {hi}")  # noqa: E731
FIELDS: dict[str, tuple[type | tuple[type, ...], Any]] = {
    "display.scale": ((int, float), _RANGE(0.5, 3.0)),
    "display.text_scale": ((int, float), _RANGE(0.5, 3.0)),
    "keyboard.layouts": (list, lambda v: "" if v and len(v) <= 4 and all(
        isinstance(x, str) and LAYOUT_RE.match(x) for x in v) else "1 to 4 layout codes such as us, ara, pk"),
    "keyboard.switch": (str, lambda v: "" if re.match(r"^[a-z_]+:[a-z_]+$", v) else "an xkb option like grp:alts_toggle"),
    "keyboard.repeat_rate": (int, _RANGE(5, 100)),
    "keyboard.repeat_delay": (int, _RANGE(100, 2000)),
    "pointer.natural_scroll": (bool, lambda v: ""),
    "pointer.sensitivity": ((int, float), _RANGE(-1.0, 1.0)),
    "idle.dim_seconds": (int, lambda v: "" if v == 0 or 30 <= v <= 86400 else "0 (never) or 30 to 86400"),
    "idle.lock_seconds": (int, lambda v: "" if v == 0 or 60 <= v <= 86400 else "0 (never) or 60 to 86400"),
    "idle.screen_off_seconds": (int, lambda v: "" if v == 0 or 60 <= v <= 86400 else "0 (never) or 60 to 86400"),
    "look.theme": (str, lambda v: "" if v in ("light", "dark") else "light or dark"),
    "look.gaps": (int, _RANGE(0, 40)),
    "look.border": (int, _RANGE(0, 10)),
    "surface.key": (str, lambda v: "" if KEY_RE.match(v) else "a key like SUPER+SPACE"),
    "surface.language": (str, lambda v: "" if v in ("en", "ur", "ar") else "en, ur or ar"),
    "surface.plain": (bool, lambda v: ""),
}

APP_FIELDS = {"name", "command", "url", "class", "key", "workspace", "autostart"}


class ProfileError(ValueError):
    """A change the profile refuses, with a reason a person can read."""


@dataclass
class Profile:
    data: dict[str, Any]

    @classmethod
    def default(cls) -> "Profile":
        return cls(copy.deepcopy(DEFAULT))

    @classmethod
    def loads(cls, text: str) -> "Profile":
        data = copy.deepcopy(DEFAULT)
        raw = tomllib.loads(text)
        for section, value in raw.items():
            if section == "apps":
                data["apps"] = value
            elif isinstance(value, dict) and section in data:
                data[section].update(value)
            else:
                raise ProfileError(f"unknown section [{section}]")
        prof = cls(data)
        prof.validate()
        return prof

    def get(self, path: str) -> Any:
        section, _, key = path.partition(".")
        return self.data[section][key]

    def validate(self) -> None:
        for path, (typ, check) in FIELDS.items():
            section, _, key = path.partition(".")
            value = self.data.get(section, {}).get(key)
            _check(path, value, typ, check)
        extra = {f"{s}.{k}" for s, sec in self.data.items() if isinstance(sec, dict) for k in sec} - set(FIELDS)
        if extra:
            raise ProfileError(f"unknown setting {sorted(extra)[0]}")
        names, keys, wss = set(), set(), set()
        for app in self.data["apps"]:
            _check_app(app)
            if app["name"].lower() in names:
                raise ProfileError(f"two apps are named {app['name']}")
            names.add(app["name"].lower())
            for field, seen in (("key", keys), ("workspace", wss)):
                if app.get(field) is not None:
                    if app[field] in seen:
                        raise ProfileError(f"{field} {app[field]} is used by two apps")
                    seen.add(app[field])
        if self.data["surface"]["key"] in keys:
            raise ProfileError(f"{self.data['surface']['key']} is the prompt key and an app key")

    # -- changes. Each returns a new Profile and leaves this one as it was.

    def set_value(self, path: str, value: Any) -> "Profile":
        if path not in FIELDS:
            raise ProfileError(f"{path} is not a setting; settings are: {', '.join(FIELDS)}")
        new = Profile(copy.deepcopy(self.data))
        section, _, key = path.partition(".")
        typ, _ = FIELDS[path]
        if typ in ((int, float),) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value) if path in ("display.scale", "display.text_scale") else value
        new.data[section][key] = value
        new.validate()
        return new

    def app(self, name: str) -> dict[str, Any] | None:
        return next((a for a in self.data["apps"] if a["name"].lower() == name.lower()), None)

    def put_app(self, app: dict[str, Any]) -> "Profile":
        """Add an app, or replace the one with the same name."""
        new = Profile(copy.deepcopy(self.data))
        new.data["apps"] = [a for a in new.data["apps"] if a["name"].lower() != str(app.get("name", "")).lower()]
        app = dict(app)
        if app.get("url") and not app.get("class"):
            app["class"] = webapp_class(app["url"])
        new.data["apps"].append(app)
        new.validate()
        return new

    def drop_app(self, name: str) -> "Profile":
        if not self.app(name):
            raise ProfileError(f"no app named {name}")
        new = Profile(copy.deepcopy(self.data))
        new.data["apps"] = [a for a in new.data["apps"] if a["name"].lower() != name.lower()]
        return new

    def dumps(self) -> str:
        out = ["# Machine profile. Exo OS renders the desktop's config files from this file.",
               "# Change it by asking Exo, or with `exo-os set`. Every change is logged and can be undone.", ""]
        for section in ("display", "keyboard", "pointer", "idle", "look", "surface"):
            out.append(f"[{section}]")
            out += [f"{k} = {_toml(v)}" for k, v in self.data[section].items()]
            out.append("")
        for app in self.data["apps"]:
            out.append("[[apps]]")
            out += [f"{k} = {_toml(v)}" for k, v in app.items() if v is not None]
            out.append("")
        return "\n".join(out)


def webapp_class(url: str) -> str:
    """The Wayland app id Chromium gives a `--app=URL` window. Seen on charminar for host-only URLs:
    https://mail.google.com/ -> chrome-mail.google.com__-Default, http://127.0.0.1:8741 ->
    chrome-127.0.0.1__-Default (port and trailing slash dropped). The form for URLs with a path is a
    guess (slashes become underscores); set `class` on the app when it is known."""
    m = re.match(r"^[a-z]+://([^/:]+)(?::\d+)?(/.*)?$", url)
    if not m:
        raise ProfileError(f"{url} is not a web address")
    host, path = m.group(1), (m.group(2) or "/")
    path = path.rstrip("/") or ""
    return f"chrome-{host}_{path.replace('/', '_')}_-Default"


def _check(path: str, value: Any, typ: Any, check: Any) -> None:
    if isinstance(value, bool) and typ is not bool:
        raise ProfileError(f"{path} must be a {_tname(typ)}")
    if not isinstance(value, typ):
        raise ProfileError(f"{path} must be a {_tname(typ)}")
    err = check(value)
    if err:
        raise ProfileError(f"{path} {err}")


def _check_app(app: dict[str, Any]) -> None:
    if not isinstance(app, dict):
        raise ProfileError("each app must be a table")
    extra = set(app) - APP_FIELDS
    if extra:
        raise ProfileError(f"app setting {sorted(extra)[0]} is not known")
    name = app.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ProfileError("an app needs a short name (letters, digits, spaces)")
    if bool(app.get("url")) == bool(app.get("command")):
        raise ProfileError(f"app {name} needs either a url or a command")
    if app.get("url") and not re.match(r"^https?://[^\s\"'\\]+$", app["url"]):
        raise ProfileError(f"app {name}: url must start with http:// or https://")
    if app.get("command") and not re.match(r"^[A-Za-z0-9_./+=:@%, -]+$", app["command"]):
        raise ProfileError(f"app {name}: command has characters that are not allowed")
    if app.get("class") is not None and not re.match(r"^[A-Za-z0-9_.:-]+$", app["class"]):
        raise ProfileError(f"app {name}: class has characters that are not allowed")
    if app.get("key") is not None and not (isinstance(app["key"], str) and KEY_RE.match(app["key"])):
        raise ProfileError(f"app {name}: key must look like SUPER+1")
    ws = app.get("workspace")
    if ws is not None and not (isinstance(ws, int) and not isinstance(ws, bool) and 1 <= ws <= 10):
        raise ProfileError(f"app {name}: workspace must be 1 to 10")
    if app.get("autostart") is not None and not isinstance(app["autostart"], bool):
        raise ProfileError(f"app {name}: autostart must be true or false")


def _tname(typ: Any) -> str:
    if typ == (int, float):
        return "number"
    return {int: "whole number", str: "text", bool: "true or false", list: "list"}.get(typ, str(typ))


def _toml(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    raise ProfileError(f"cannot write {v!r}")

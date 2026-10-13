"""Root-owned HTTPS profiles. No request may supply an authority or headers."""
from __future__ import annotations

import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..sandbox.policy import trusted_path

MAX_BODY = 32 * 1024
MAX_RESPONSE = 64 * 1024
MAX_WIRE = 256 * 1024
TIMEOUT = 10
WORKERS = 4
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})


class EgressError(ValueError):
    pass


def path_check(value: str) -> str:
    # Deliberately omit query, fragment and %-encoding: intermediaries cannot decode
    # a second authority, separator, dot segment or prefix escape from this grammar.
    if (not isinstance(value, str) or len(value) > 2048 or
            not re.fullmatch(r"/[A-Za-z0-9_./~-]*", value) or "//" in value or
            any(segment in (".", "..") for segment in value.split("/"))):
        raise EgressError("path must be a canonical absolute path without encoding or query")
    return value


@dataclass(frozen=True)
class Profile:
    origin: str
    methods: tuple[str, ...]
    path_prefix: str
    credential: str

    def __post_init__(self):
        try:
            parsed = urlsplit(self.origin)
            port = parsed.port
        except (ValueError, TypeError):
            raise EgressError("invalid HTTPS origin") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or
                parsed.password is not None or parsed.path or parsed.query or parsed.fragment or
                not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", self.origin) or
                (port is not None and not 1 <= port <= 65535)):
            raise EgressError("profile must name an exact HTTPS origin")
        path_check(self.path_prefix)
        if self.path_prefix != "/" and self.path_prefix.endswith("/"):
            raise EgressError("prefix must omit trailing slash")
        if (not isinstance(self.methods, tuple) or not self.methods or
                any(not isinstance(m, str) or m not in METHODS for m in self.methods) or
                not isinstance(self.credential, str) or not NAME.fullmatch(self.credential)):
            raise EgressError("invalid methods or credential name")

    def permits(self, method: str, path: str) -> bool:
        return method in self.methods and (self.path_prefix == "/" or path == self.path_prefix or
                                          path.startswith(self.path_prefix + "/"))


def load_policy(path: Path = Path("/etc/exo-os/egress.toml")) -> dict[str, Profile]:
    trusted_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
        raise EgressError("invalid egress policy file")
    raw = tomllib.loads(path.read_text())
    if set(raw) != {"profiles"} or not isinstance(raw["profiles"], dict) or len(raw["profiles"]) > 64:
        raise EgressError("policy must contain only profiles")
    profiles = {}
    for name, item in raw["profiles"].items():
        if not NAME.fullmatch(name) or not isinstance(item, dict) or set(item) != {
                "origin", "methods", "path_prefix", "credential"} or not isinstance(item["methods"], list):
            raise EgressError("invalid profile")
        profiles[name] = Profile(**{**item, "methods": tuple(item["methods"])})
    return profiles


def validate_request(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"version", "nonce", "request_id", "profile", "method", "path", "body", "expected_origin"}:
        raise EgressError("unexpected request fields")
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise EgressError("unsupported protocol")
    for key in ("nonce", "request_id", "profile"):
        if not isinstance(raw[key], str) or not NAME.fullmatch(raw[key]):
            raise EgressError("invalid request identifier")
    if not isinstance(raw["method"], str) or raw["method"] not in METHODS:
        raise EgressError("invalid HTTP method")
    if not isinstance(raw["expected_origin"], str) or len(raw["expected_origin"]) > 256:
        raise EgressError("invalid expected origin")
    Profile(raw["expected_origin"], ("POST",), "/", "assertion")
    path_check(raw["path"])
    if not isinstance(raw["body"], str) or len(raw["body"].encode()) > MAX_BODY:
        raise EgressError("request body exceeds limit")
    return raw

"""One strict bounded request/reply per authenticated local connection."""
from exo.ledger_authority import (
    AuthorityError, MAX_BATCH, MAX_DOCUMENT, NAME, canonical, validate_expected_head, validate_intent,
)
from ..sysd.protocol import decode, read_line

MAX_WIRE = MAX_DOCUMENT + 1
GREETING = b"exo-ledger-v1\n"


def encode(value):
    # Match the core's UTF-8 byte bounds, avoiding ASCII-escape expansion on replay.
    return canonical(value) + b"\n"


def request(raw):
    if not isinstance(raw, dict):
        raise AuthorityError("invalid authority request")
    common = {"version", "nonce", "stream", "writer", "op"}
    fields = {"append": {"operation_id", "expected_head", "intent"}, "lookup": {"operation_id"},
              "head": {"seq"}, "read_since": {"seq", "limit"}}
    if (not isinstance(raw.get("op"), str) or raw["op"] not in fields
            or set(raw) != common | fields[raw["op"]]
            or type(raw["version"]) is not int or raw["version"] != 1):
        raise AuthorityError("unsupported authority request")
    for key in ("nonce", "stream", "writer"):
        if not isinstance(raw[key], str) or not NAME.fullmatch(raw[key]):
            raise AuthorityError("invalid request identifier")
    if "operation_id" in raw and (not isinstance(raw["operation_id"], str) or not NAME.fullmatch(raw["operation_id"])):
        raise AuthorityError("invalid operation id")
    if raw["op"] == "append":
        validate_expected_head(raw["expected_head"])
        validate_intent(raw["intent"])
    if "seq" in raw and raw["seq"] is not None and (type(raw["seq"]) is not int or raw["seq"] < 0):
        raise AuthorityError("invalid sequence")
    if raw["op"] == "read_since" and (raw["seq"] is None or type(raw["limit"]) is not int or not 1 <= raw["limit"] <= MAX_BATCH):
        raise AuthorityError("invalid replay bound")
    # Defense in depth: encode() already bounds the document through canonical(),
    # so this cannot fire unless encode() changes. It stays as a second bound.
    if len(encode(raw)) > MAX_WIRE:
        raise AuthorityError("authority request exceeds bound")
    return raw


__all__ = ["decode", "encode", "read_line", "request", "MAX_WIRE", "GREETING"]

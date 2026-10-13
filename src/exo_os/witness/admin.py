"""Explicit root-only witness initialization, publication and cached/fresh status."""
from __future__ import annotations

import argparse
import datetime
import os
import secrets
from pathlib import Path

from .process import MAX_EXPORT, Workers, snapshots
from .state import (
    BASE,
    CREDENTIALS,
    MARKER,
    WitnessError,
    decode,
    directory,
    encoded,
    established,
    evidence,
    exists,
    monotonic,
    persist,
    policy_hash,
    read_file,
    validate_evidence,
    validate_policy,
    write_new,
)


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def bound_authority(policy):
    """Public protected manifest only; root never loads the signing journal or keys."""
    from ..ledgerd.policy import MANIFEST, validate_manifest
    with directory(MANIFEST.parent) as fd:
        manifest = validate_manifest(decode(read_file(fd, MANIFEST.name, mode=0o644)))
    stream = policy["identity"]["stream_id"]
    if stream not in manifest["streams"] or manifest["streams"][stream]["identity"] != policy["identity"]:
        raise WitnessError("witness policy does not bind the provisioned authority")


def check_credentials(credentials):
    """Offline SSL parsing/key-pair validation; no socket, discovery or trust-store defaults."""
    import ssl
    with snapshots(credentials, b"", 0) as path:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=credentials["ca.pem"].decode("ascii"))
        context.load_cert_chain(str(Path(path) / "client.pem"), str(Path(path) / "client.key"), password="")


def initialize(bundle, *, base=BASE):
    """Publish enrollment last; missing/partial existing enrollment requires explicit repair."""
    if os.geteuid() != 0:
        raise WitnessError("witness administration requires root")
    with directory(Path(bundle), mode=0o700) as source:
        policy = validate_policy(decode(read_file(source, "policy.json")))
        receipt = decode(read_file(source, "receipt.json"))
        credentials = {name: read_file(source, name) for name in CREDENTIALS}
    bound_authority(policy)  # a fresh image's empty store is never treated as enrolled
    initial = validate_evidence(policy, evidence(policy, policy["genesis"], receipt, policy["genesis"], now()))
    check_credentials(credentials)
    base = Path(base)
    with directory(base.parent) as parent:
        if exists(parent, MARKER) or exists(parent, base.name):
            raise WitnessError("witness state already exists; explicit administrator repair required")
        os.mkdir(base.name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
        with directory(base, mode=0o700) as fd:
            for name, raw in {"policy.json": encoded(policy), "current.json": encoded(initial),
                              "previous.json": encoded(initial), **credentials}.items():
                write_new(fd, name, raw)
            os.fsync(fd)
        write_new(parent, MARKER, encoded({"version": 1, "policy_hash": policy_hash(policy)}))
        os.fsync(parent)
    return {"state": "initialized", "witnessed_seq": 0, "fresh": False}


class Export:
    def __init__(self, policy, raw):
        from exo.ledger_authority import verify_checkpoint, verify_checkpoint_successor
        if len(raw) > MAX_EXPORT + 8193:
            raise WitnessError("checkpoint export exceeds fixed byte bound")
        header, separator, self.data = raw.partition(b"\n")
        if not separator or len(header) > 8192 or len(self.data) > MAX_EXPORT:
            raise WitnessError("invalid checkpoint export framing")
        captured = decode(header)
        if not isinstance(captured, dict) or set(captured) != {"target"}:
            raise WitnessError("checkpoint export has no captured target")
        self.target = captured["target"]
        verify_checkpoint(policy["identity"], self.target)
        previous = None
        count = 0
        for line in self.data.splitlines(keepends=True):
            if not line.endswith(b"\n") or len(line) > 8193:
                raise WitnessError("invalid checkpoint export line")
            checkpoint = decode(line)
            if previous is None:
                if checkpoint != policy["genesis"]:
                    raise WitnessError("checkpoint export does not start at pinned genesis")
            else:
                verify_checkpoint_successor(policy["identity"], checkpoint, previous)
            previous = checkpoint
            count += 1
        if previous != self.target or count != self.target["seq"] + 1:
            raise WitnessError("checkpoint export does not reach exactly its captured target")

    def checkpoint(self, seq):
        if type(seq) is not int or not 0 <= seq <= self.target["seq"]:
            raise WitnessError("checkpoint is beyond captured authority target", "conflict")
        for index, line in enumerate(self.data.splitlines()):
            if index == seq:
                return decode(line)
        raise WitnessError("checkpoint export is incomplete")


def compare(policy, exported, current, receipt):
    from exo.ledger_authority.witness import verify_receipt
    if receipt["seq"] < current["receipt"]["seq"]:
        raise WitnessError("fresh witness regressed behind saved receipt", "conflict")
    if receipt["seq"] == current["receipt"]["seq"] and receipt != current["receipt"]:
        raise WitnessError("fresh witness conflicts with saved receipt", "conflict")
    if current["receipt"]["seq"] <= exported.target["seq"] and exported.checkpoint(current["receipt"]["seq"]) != current["checkpoint"]:
        raise WitnessError("authority conflicts with saved witnessed prefix", "conflict")
    if receipt["seq"] > exported.target["seq"]:
        return "fresh_witness_ahead"
    try:
        verify_receipt(policy["identity"], exported.checkpoint(receipt["seq"]), receipt, policy["witness_pub"])
    except ValueError as exc:
        raise WitnessError("fresh witness conflicts with captured authority prefix", "conflict") from exc
    return "fresh_match_at_snapshot" if receipt["seq"] == exported.target["seq"] else "fresh_witness_behind"


def observation(policy, raw_reply, nonce, *, publish=False):
    """Verify every network envelope in the trusted parent, including query-only replies."""
    from exo.ledger_authority.witness_transport import verify_head
    try:
        reply = decode(raw_reply)
    except WitnessError as exc:
        raise WitnessError("invalid witness helper response", "unavailable") from exc
    if not isinstance(reply, dict) or set(reply) != {"receipt", "head"}:
        raise WitnessError("invalid witness helper response", "unavailable")
    if not publish and reply["receipt"] is not None:
        raise WitnessError("fresh status returned an unexpected publication receipt", "unavailable")
    try:
        receipt = verify_head(policy["identity"], policy["genesis"], reply["head"], policy["witness_pub"], nonce)
    except ValueError as exc:
        raise WitnessError("untrusted or replayed witness head", "unavailable") from exc
    return reply, receipt


def operate(*, publish=False, fresh=False, base=BASE, workers=None):
    if os.geteuid() != 0:
        raise WitnessError("witness administration requires root")
    with established(base) as loaded:
        if loaded is None:
            return {"state": "disabled", "fresh": False}
        fd, policy, current, credentials = loaded
        bound_authority(policy)
        check_credentials(credentials)
        if not publish and not fresh:
            return {"state": "cached_receipt", "fresh": False, "evidence": current,
                    "message": "Saved signed receipt; current witness state and independence are not established."}
        workers = workers or Workers()
        exported = Export(policy, workers.export(policy))  # every link validated before any network child
        captured_at = now()
        if publish:
            if current["receipt"]["seq"] > exported.target["seq"]:
                raise WitnessError("authority is behind saved witness evidence; publication refused", "conflict")
            if exported.checkpoint(current["receipt"]["seq"]) != current["checkpoint"]:
                raise WitnessError("authority conflicts with saved witnessed prefix", "conflict")
        # A separate query-only worker returns to the root parent before any
        # checkpoint bytes are given to a publication worker. A regressed or
        # conflicting witness must not repair itself before root observes it.
        if publish:
            pre_nonce = secrets.token_hex(32)
            _, pre_receipt = observation(policy, workers.network(
                policy, credentials, b"", publish=False, nonce=pre_nonce), pre_nonce)
            pre_state = compare(policy, exported, current, pre_receipt)
            if pre_state == "fresh_witness_ahead":
                raise WitnessError("witness is ahead of captured export; publication refused", "conflict")
        nonce = secrets.token_hex(32)  # parent challenge, never selected by a network child
        if publish and nonce == pre_nonce:
            raise WitnessError("witness challenges must be distinct", "unavailable")
        reply, receipt = observation(policy, workers.network(
            policy, credentials, exported.data if publish else b"", publish=publish, nonce=nonce), nonce,
            publish=publish)
        from exo.ledger_authority.witness import verify_receipt
        state = compare(policy, exported, current, receipt)
        observed_at = now()
        result = {"state": state, "fresh": True, "captured_at": captured_at, "observed_at": observed_at,
                  "authority_target": exported.target, "receipt": receipt,
                  "message": "This challenged observation describes this snapshot only; independence is an operator prerequisite."}
        if publish:
            try:
                verify_receipt(policy["identity"], exported.target, reply["receipt"], policy["witness_pub"])
            except ValueError as exc:
                raise WitnessError("untrusted publication receipt", "unavailable") from exc
            if state != "fresh_match_at_snapshot" or receipt != reply["receipt"]:
                raise WitnessError("witness is ahead of captured export or changed during publication", "conflict")
            updated = evidence(policy, exported.target, receipt, exported.target, observed_at)
            monotonic(current, updated)
            persist(fd, policy, current, updated)
            result["state"] = "published"
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("initialize")
    init.add_argument("--bundle", type=Path, required=True)
    sub.add_parser("publish")
    status = sub.add_parser("status")
    status.add_argument("--fresh", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = initialize(args.bundle) if args.command == "initialize" else operate(
            publish=args.command == "publish", fresh=getattr(args, "fresh", False))
    except (ValueError, OSError, RuntimeError) as exc:
        result = {"state": getattr(exc, "state", "configuration_error"), "fresh": False,
                  "message": str(exc) if isinstance(exc, WitnessError) else "Invalid or unavailable protected witness state."}
    print(encoded(result).decode(), end="")
    return 1 if result["state"] in ("configuration_error", "unavailable", "conflict", "fresh_witness_ahead") else 0


if __name__ == "__main__":
    raise SystemExit(main())

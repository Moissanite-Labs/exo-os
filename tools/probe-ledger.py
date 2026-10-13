#!/usr/bin/env python3
"""Exercise the installed ledger authority only in an explicitly disposable VM.

The harness stops exod first. Only this VM's synthetic projection is corrupted;
the protected history is never changed except for two ordinary test appends.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import sqlite3
import stat
import subprocess
import sys
import uuid
from pathlib import Path

LEDGER = Path("/var/lib/exo/exo/principals/exo/ledger.sqlite")
CHECKS = (
    "private_state_unreadable", "append_with_service_identity", "idempotent_operation",
    "truncation_detected", "explicit_projection_recovery", "edited_row_detected",
    "unconfirmed_tail_detected", "direct_key_write_refused",
)
UNAVAILABLE_CHECKS = ("missing_signer_refuses_append", "missing_signer_refuses_verification")


def cli_verify(expected):
    """Both normal user commands must detect corruption without repairing it."""
    with sqlite3.connect(LEDGER) as db:
        before = list(db.execute("SELECT * FROM events ORDER BY seq"))
        triggers = list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))
    for command in (("log", "verify"), ("memory", "verify", "--json")):
        result = subprocess.run(["/opt/exo/bin/exo", *command], text=True, capture_output=True, timeout=120)
        reply = json.loads(result.stdout)
        if command[0] == "memory":
            reply = reply["ledgers"]["exo"]
        assert reply.get("state") == expected, (command, reply)
        assert reply.get("ok") is (expected == "verified_prefix"), (command, reply)
        assert (result.returncode == 0) is (expected == "verified_prefix"), result.stderr
    with sqlite3.connect(LEDGER) as db:
        assert list(db.execute("SELECT * FROM events ORDER BY seq")) == before, "verification changed rows"
        assert list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")) == triggers, "verification changed triggers"


def mutate(db, trigger, sql, parameters=()):
    """Keep the append-only trigger present, so signatures/history must detect the edit."""
    schema = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)).fetchone()[0]
    db.execute(f'DROP TRIGGER "{trigger}"')
    db.execute(sql, parameters)
    db.execute(schema)
    db.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-acceptance", action="store_true")
    parser.add_argument("--signer-unavailable", action="store_true")
    args = parser.parse_args(argv)
    if not args.vm_acceptance or os.environ.get("EXO_OS_VM_ACCEPTANCE") != "1":
        parser.error("requires --vm-acceptance and EXO_OS_VM_ACCEPTANCE=1 inside the disposable VM")
    if sys.platform != "linux" or not Path("/run/systemd/system").is_dir():
        parser.error("requires a booted Linux systemd VM")
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "exo":
        parser.error("run as the exo account, never root")
    expected = UNAVAILABLE_CHECKS if args.signer_unavailable else CHECKS
    checks = []

    def check(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "status": "PASS", "detail": detail})
        except Exception as exc:
            checks.append({"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"[:240]})

    try:
        from exo import crypto
        from exo.ledger import Ledger
        from exo.ledger_authority import AuthorityError
        from exo.ledger_authority.factory import open_authority, open_writable_ledger

        assert os.environ.get("EXO_LEDGER_AUTHORITY") == "exo-os", "installed environment lacks authority"
        assert os.environ.get("EXO_LEDGER_AUTHORITY_REQUIRED") == "1", "authority is not required"
        token = uuid.uuid4().hex

        def missing_signer_append():
            with sqlite3.connect(LEDGER) as db:
                before = list(db.execute("SELECT * FROM events ORDER BY seq"))
            try:
                with open_writable_ledger(LEDGER, "exo-os") as ledger:
                    ledger.append("note", {"acceptance": token, "must_not_commit": True})
            except AuthorityError:
                pass
            else:
                raise AssertionError("append succeeded without the authority")
            with sqlite3.connect(LEDGER) as db:
                assert list(db.execute("SELECT * FROM events ORDER BY seq")) == before, "failed append changed the projection rows"
            return "unavailable signer refused before any event could be appended"

        def missing_signer_verify():
            cli_verify("unverifiable")
            return "both verification commands refused an unavailable signer without repairing rows"

        if args.signer_unavailable:
            check(expected[0], missing_signer_append)
            check(expected[1], missing_signer_verify)
        else:
            authority = open_authority(LEDGER, "exo-os")
            event = None

            def private_unreadable():
                manifest = Path("/var/lib/exo-ledger/manifest.json")
                assert manifest.stat().st_uid == 0 and not manifest.stat().st_mode & 0o022
                assert os.access(manifest, os.W_OK) is False
                private = manifest.parent / "private"
                info = private.stat()
                assert info.st_uid == pwd.getpwnam("exo-ledger").pw_uid and stat.S_IMODE(info.st_mode) == 0o700
                try:
                    list(private.iterdir())
                except PermissionError:
                    pass
                else:
                    raise AssertionError("desktop user can read the protected signer state")
                for stream, entry in json.loads(manifest.read_text())["streams"].items():
                    names = ["checkpoint.key", "journal.sqlite"]
                    names.extend(hashlib.sha256(writer.encode()).hexdigest() + ".key" for writer in entry["identity"]["writers"])
                    for name in names:
                        try:
                            with (private / stream / name).open("rb"):
                                pass
                        except PermissionError:
                            pass
                        else:
                            raise AssertionError("desktop user can open a known private key or journal path")
                return "root identity is read-only; private keys and journal are inaccessible to the desktop UID"

            def append():
                nonlocal event
                with open_writable_ledger(LEDGER, "exo-os") as ledger:
                    event = ledger.append("note", {"acceptance": token}, operation_id=token)
                record = authority.lookup(token)
                assert record["event"] == event
                cli_verify("verified_prefix")
                return "real authenticated service append, operation lookup and both normal verifiers agree"

            def idempotent():
                old = authority.head()
                intent = {"author": "exo-os", "kind": "note", "body": {"idempotence": token}, "session": None, "refs": []}
                operation = uuid.uuid4().hex
                expected_head = {"seq": old["seq"], "chain": old["chain"]}
                first = authority.append(operation, expected_head, intent)
                again = authority.append(operation, expected_head, intent)
                assert first == again and authority.head()["seq"] == old["seq"] + 1
                try:
                    authority.append(operation, expected_head, {**intent, "body": {"changed": token}})
                except AuthorityError:
                    pass
                else:
                    raise AssertionError("operation ID accepted a changed intent")
                with open_writable_ledger(LEDGER, "exo-os"):
                    pass
                return "same operation returns its committed record; changed intent refuses without another append"

            truncated_head = None

            def truncate():
                nonlocal truncated_head
                truncated_head = authority.head()
                with sqlite3.connect(LEDGER) as db:
                    mutate(db, "events_no_delete", "DELETE FROM events WHERE seq=?", (truncated_head["seq"],))
                cli_verify("missing_tail")
                assert authority.head() == truncated_head
                return "deleting the last signed row is detected by both commands, with triggers intact"

            def recover():
                assert truncated_head is not None, "truncation did not run"
                with open_writable_ledger(LEDGER, "exo-os") as ledger:
                    assert ledger.head().seq == truncated_head["seq"]
                assert authority.head() == truncated_head, "projection recovery appended another event"
                cli_verify("verified_prefix")
                return "explicit writer synchronization restores the exact committed tail without re-signing"

            def edit():
                assert event is not None, "synthetic append did not run"
                with sqlite3.connect(LEDGER) as db:
                    original = db.execute("SELECT body FROM events WHERE id=?", (event["id"],)).fetchone()[0]
                    mutate(db, "events_no_update", "UPDATE events SET body=? WHERE id=?", ('{"forged":true}', event["id"]))
                    try:
                        cli_verify("divergent")
                    finally:
                        mutate(db, "events_no_update", "UPDATE events SET body=? WHERE id=?", (original, event["id"]))
                return "editing a signed body is detected without silently restoring or accepting the edit"

            def add_tail():
                with sqlite3.connect(LEDGER) as db:
                    seq = db.execute("SELECT max(seq) FROM events").fetchone()[0] + 1
                    db.execute("INSERT INTO events SELECT ?,?,ts,author,kind,body,session,refs,prev,pub,sig,chain FROM events WHERE seq=?",
                               (seq, uuid.uuid4().hex, seq - 1))
                    db.commit()
                    try:
                        cli_verify("unconfirmed_tail")
                    finally:
                        mutate(db, "events_no_delete", "DELETE FROM events WHERE seq=?", (seq,))
                return "an extra desktop-written row is rejected against the protected history"

            def direct_key():
                path = LEDGER.parent / ("must-not-exist-" + token + ".sqlite")
                try:
                    Ledger(path, crypto.keygen())
                except AuthorityError:
                    pass
                else:
                    raise AssertionError("direct-key constructor bypassed required authority")
                assert not path.exists()
                return "required mode refuses a caller-owned signer before creating another ledger"

            for name, fn in zip(CHECKS, (private_unreadable, append, idempotent, truncate, recover, edit, add_tail, direct_key), strict=True):
                check(name, fn)
    except Exception as exc:
        for name in expected[len(checks):]:
            checks.append({"name": name, "status": "FAIL", "detail": f"setup refused: {type(exc).__name__}: {exc}"[:240]})
    print(json.dumps({"checks": checks}, separators=(",", ":")))
    return 0 if len(checks) == len(expected) and all(item["status"] == "PASS" for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""The destructive ledger probe must refuse accidental use and preserve verification evidence."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

PROBE = Path(__file__).resolve().parents[1] / "tools/probe-ledger.py"


@pytest.mark.parametrize("flag,optin", [(False, ""), (True, ""), (False, "1")])
def test_probe_requires_both_disposable_vm_optins(flag, optin):
    result = subprocess.run([sys.executable, str(PROBE), *(["--vm-acceptance"] if flag else [])],
                            env={**os.environ, "EXO_OS_VM_ACCEPTANCE": optin}, capture_output=True, text=True)
    assert result.returncode == 2 and "requires --vm-acceptance" in result.stderr
    assert result.stdout == ""


def load_probe():
    spec = importlib.util.spec_from_file_location("ledger_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("repair", ["rows", "triggers"])
def test_verification_probe_rejects_silent_evidence_repair(tmp_path, monkeypatch, repair):
    module = load_probe()
    module.LEDGER = tmp_path / "ledger.sqlite"
    with sqlite3.connect(module.LEDGER) as db:
        db.executescript("CREATE TABLE events(seq INTEGER PRIMARY KEY, body TEXT); INSERT INTO events VALUES(1,'original');")

    def command(args, **kwargs):
        with sqlite3.connect(module.LEDGER) as db:
            if repair == "rows":
                db.execute("UPDATE events SET body='repaired'")
            else:
                db.execute("CREATE TRIGGER IF NOT EXISTS repaired BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'no'); END")
        result = {"ok": False, "state": "missing_tail"}
        if args[1] == "memory":
            result = {"ledgers": {"exo": result}}
        return SimpleNamespace(returncode=1, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(module.subprocess, "run", command)
    with pytest.raises(AssertionError, match="verification changed"):
        module.cli_verify("missing_tail")


def test_probe_requires_both_public_verifiers_to_reject_corruption(tmp_path, monkeypatch):
    module = load_probe()
    module.LEDGER = tmp_path / "ledger.sqlite"
    with sqlite3.connect(module.LEDGER) as db:
        db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, body TEXT)")

    def command(args, **kwargs):
        result = {"ok": False, "state": "missing_tail"}
        code = 1
        if args[1] == "memory":
            result = {"ledgers": {"exo": {"ok": True, "state": "verified_prefix"}}}
            code = 0
        return SimpleNamespace(returncode=code, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(module.subprocess, "run", command)
    with pytest.raises(AssertionError):
        module.cli_verify("missing_tail")

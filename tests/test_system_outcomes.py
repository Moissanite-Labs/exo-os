"""A completed privileged action with an audit fault must never look safe to repeat."""
import json

import pytest

from exo_os import cli, plugin
from exo_os.sysd.client import CompletedButUnrecorded, SysdClient


@pytest.fixture
def completed(monkeypatch, tmp_path):
    outcome = CompletedButUnrecorded(
        {"installed": ["htop"], "pre": 11, "post": 12},
        {"execution": "completed", "request_id": "a" * 64}, tmp_path / "receipt.json", "ledger disk full")

    def call(*args, **kwargs):
        raise outcome

    monkeypatch.setattr(SysdClient, "call", call)
    monkeypatch.setattr(cli, "engine", lambda: None)
    return outcome


def test_cli_distinguishes_completed_action_from_failure(completed, capsys):
    assert cli.main(["--json", "system", "install", "htop"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["completed"] is True and result["retryable"] is False
    assert result["result"]["installed"] == ["htop"]
    assert "Do not retry" in result["message"]


@pytest.mark.parametrize("handler,args", [
    (plugin.system_package_install, {"names": ["htop"]}),
    (plugin.system_snapshot_undo, {"pre": 11, "post": 12}),
])
def test_plugin_returns_completed_audit_outcome_instead_of_retryable_error(completed, handler, args):
    result = json.loads(handler(args))
    assert result["completed"] is True and result["retryable"] is False
    assert result["receipt"] == completed.receipt


def test_reconcile_command_records_receipts_without_repeating_actions(completed, monkeypatch, capsys):
    summary = {"recorded": ["receipt-id"], "already_recorded": [], "rejected": [],
               "errors": [], "root_audit_errors": []}
    monkeypatch.setattr(SysdClient, "recover_pending", lambda self: summary)
    assert cli.main(["--json", "system", "reconcile"]) == 0
    assert json.loads(capsys.readouterr().out) == summary


def test_reconcile_reports_unresolved_audit_fault(completed, monkeypatch, capsys):
    summary = {"recorded": [], "already_recorded": [], "rejected": [],
               "errors": ["ledger unavailable"], "root_audit_errors": []}
    monkeypatch.setattr(SysdClient, "recover_pending", lambda self: summary)
    assert cli.main(["--json", "system", "reconcile"]) == 1
    assert json.loads(capsys.readouterr().out)["errors"] == ["ledger unavailable"]

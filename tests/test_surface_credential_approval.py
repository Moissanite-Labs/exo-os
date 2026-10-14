"""The approved egress operation is inspectable in full, independently of the GTK display."""
import json

import pytest

from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo_os.surface.model import SurfaceModel

from exo_os.egress import policy


@pytest.fixture
def destination(monkeypatch):
    profile = policy.Profile("https://api.example.test:8443", ("POST",), "/messages", "hidden_token")
    monkeypatch.setattr(policy, "load_policy", lambda: {"work_messages": profile})
    return profile


def request(**changes):
    return {"profile": "work_messages", "request_id": "operation_123",
            "expected_origin": "https://api.example.test:8443", "method": "POST", "path": "/messages/send",
            "body": "مرحبا <b>literal markup</b>\n" + "long payload " * 1500 + "\u202ePAYLOAD_SUFFIX", **changes}


def approval(args, *, plain=True):
    model = SurfaceModel(plain=plain)
    model.set_connected(True)
    model.add_approval({"request_id": "approval_123", "summary": "Send to misleading model recipient?",
                        "binding": "exact_request_binding", "options": ["allow", "deny", "allow_always"],
                        "call": ToolCall(id="tool_123", name="credential_request", arguments=args)})
    return model


@pytest.mark.parametrize("plain", [True, False])
def test_full_credential_operation_is_visible_and_one_shot(destination, plain):
    args = request()
    model = approval(args, plain=plain)
    card = model.approval
    assert destination.origin in card.summary and "misleading model recipient" not in card.summary
    displayed = json.loads(card.detail.split("\n", 1)[1])
    assert displayed["approval_request_id"] == "approval_123"
    assert displayed["request"] == args  # full profile, origin, method, path, body and operation request_id
    assert "PAYLOAD_SUFFIX" in card.detail and len(card.detail) > 18000
    assert "\\u202e" in card.detail and "\u202e" not in card.detail  # control characters cannot hide text
    assert "<b>literal markup</b>" in card.detail and "مرحبا" in card.detail
    assert "hidden_token" not in card.detail  # policy's credential filename is not a preview field
    assert card.options == ["allow", "deny"] and model.answer("allow_always") is None
    assert model.answer("allow") == s.ApprovalRespond(request_id="approval_123", decision="allow",
                                                       client="exo-surface", binding="exact_request_binding")


@pytest.mark.parametrize("plain", [True, False])
@pytest.mark.parametrize("failure", ["missing_policy", "untrusted_policy", "unknown_profile", "changed_origin", "wrong_method"])
def test_unverified_destination_cannot_be_approved(monkeypatch, destination, plain, failure):
    args = request()
    if failure in ("missing_policy", "untrusted_policy"):
        from exo_os.sandbox.policy import SandboxError
        def unavailable():
            raise FileNotFoundError if failure == "missing_policy" else SandboxError("untrusted file")
        monkeypatch.setattr(policy, "load_policy", unavailable)
    elif failure == "unknown_profile":
        monkeypatch.setattr(policy, "load_policy", lambda: {})
    elif failure == "changed_origin":
        args["expected_origin"] = "https://different.example.test"
    else:
        args["method"] = "DELETE"
    model = approval(args, plain=plain)
    assert model.approval.summary.startswith("This request cannot be approved.")
    assert model.approval.options == ["deny"]
    assert model.answer("allow") is None and model.answer("allow_always") is None
    assert json.loads(model.approval.detail.split("\n", 1)[1])["request"] == args


@pytest.mark.parametrize("change", [{"expected_origin": None}, {"body": {"not": "a string"}},
                                   {"path": "//evil.test"}, {"recipient": "model-chosen host"}])
def test_invalid_request_never_produces_an_approval(destination, change):
    model = approval(request(**change))
    assert model.approval.options == ["deny"] and model.answer("allow") is None


def test_missing_origin_and_default_empty_body(destination):
    args = request()
    del args["expected_origin"]
    model = approval(args)
    assert model.approval.options == ["deny"] and model.answer("allow") is None
    del args["body"]
    args["expected_origin"] = destination.origin
    model = approval(args)
    displayed = json.loads(model.approval.detail.split("\n", 1)[1])
    assert displayed["request"]["body"] == "" and model.approval.options == ["allow", "deny"]


@pytest.mark.parametrize("plain", [True, False])
def test_urdu_is_readable_while_unsafe_controls_are_exact_and_visible(destination, plain):
    urdu = "احمد کو یہ پیغام بھیجیں۔"
    body = urdu + "\n" + "\u200b\u2066\u202e\u2069\u2028\u0085\u0001" + "PAYLOAD_SUFFIX"
    card = approval(request(body=body), plain=plain).approval
    assert urdu in card.detail
    for escaped in ("\\u200b", "\\u2066", "\\u202e", "\\u2069", "\\u2028", "\\u0085", "\\u0001"):
        assert escaped in card.detail
    for unsafe in ("\u200b", "\u2066", "\u202e", "\u2069", "\u2028", "\u0085", "\u0001"):
        assert unsafe not in card.detail
    assert json.loads(card.detail.split("\n", 1)[1])["request"]["body"] == body
    assert "PAYLOAD_SUFFIX" in card.detail


def test_uncertain_answer_recovery_preserves_full_credential_details(destination):
    model = approval(request())
    original = model.approval.detail
    model.answer("allow")
    model.approval_result("approval_123")
    model.reconcile_approvals([{"request_id": "approval_123"}], 100)
    assert model.approval.detail == original
    assert model.approval.options == ["allow", "deny"]
    assert model.answer("allow").binding == "exact_request_binding"

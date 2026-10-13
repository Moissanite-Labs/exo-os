"""Spending stays inspectable without generation or an approval to raise limits."""
import asyncio

import pytest

from exo.protocol import schema as s
from exo_os.surface.conn import Connection
from exo_os.surface.model import SurfaceModel
from exo_os.surface.spend import UNAVAILABLE, describe_spend


def status(**changes):
    return {"state": "offline", "daily_cap_usd": "0.000000000", "monthly_cap_usd": "0.000000000",
            "day_charged_usd": "0.000000000", "month_charged_usd": "0.000000000",
            "pending_reserved_usd": "0.000000000", "pending_count": 0, **changes}


@pytest.mark.parametrize("query", ["budget", "Spending", "model budget"])
@pytest.mark.parametrize("plain", [True, False])
def test_budget_is_a_read_only_protocol_query_without_a_chat_session(query, plain):
    model = SurfaceModel(plain=plain)
    model.set_connected(True)
    model.set_input(query)
    action = model.submit()
    assert isinstance(action.command, s.ModelsList)
    assert model.session is None
    assert not model.outbox
    model.model_spend(status())
    text = "\n".join(line.text for line in model.visible_lines())
    assert "Paid models are disabled." in text
    assert "Daily limit: $0.00. Monthly limit: $0.00." in text
    assert not model.approvals


def test_budget_word_can_still_be_sent_as_a_question_answer():
    model = SurfaceModel()
    model.set_connected(True)
    model.question = s.QuestionAsked(question_id="q", text="Which report?")
    model.set_input("budget")
    action = model.submit()
    assert isinstance(action.command, s.QuestionAnswer)
    assert action.command.answer == "budget"


def test_budget_readout_preserves_small_charges_and_retained_uncertainty():
    text = describe_spend(status(state="stopped", day_charged_usd="0.000000001",
                                 monthly_cap_usd="5.000000000", pending_count=2,
                                 pending_reserved_usd="1.230000000"))
    assert "$0.000000001 today" in text
    assert "Unsettled calls: 2, with $1.23 still reserved" in text
    assert "Approval replies do not raise these limits" in text


@pytest.mark.parametrize("value", [None, {}, status(state="unavailable"), status(day_charged_usd="NaN"),
                                  status(monthly_cap_usd=5), status(pending_count=True),
                                  status(pending_reserved_usd="-1")])
def test_missing_or_malformed_accounting_never_appears_as_zero(value):
    assert describe_spend(value) == UNAVAILABLE
    assert "$0" not in describe_spend(value)


def test_spend_stop_has_no_retry_prompt_after_turn_completion():
    model = SurfaceModel()
    model.set_connected(True)
    for seq, body in enumerate((s.TurnStarted(turn_id="t"),
                               s.Error(code="model_spend_stopped", message="cap refused", retryable=False),
                               s.TurnCompleted(turn_id="t", stop_reason="error")), 1):
        model.apply(s.Event.of(body, seq))
    text = "\n".join(line.text for line in model.visible_lines())
    assert "Type budget" in text
    assert "try again" not in text.lower()
    model.set_input("budget")
    assert isinstance(model.submit().command, s.ModelsList)


def test_connection_delivers_spending_result_without_a_turn_or_approval(tmp_path):
    class Listener:
        def on_model_spend(self, value):
            self.value = value

    class Client:
        def __init__(self):
            self.calls = []

        async def send(self, body):
            self.calls.append(body)
            return s.Response(id="budget", result={"models": [], "spend": status(state="stopped")})

    listener, client = Listener(), Client()
    conn = Connection(listener, socket=tmp_path / "socket", state=tmp_path / "state",
                      post=lambda fn, *args: fn(*args))
    conn._client = client
    asyncio.run(conn._send(s.ModelsList(), client=client))
    assert len(client.calls) == 1 and isinstance(client.calls[0], s.ModelsList)
    assert listener.value == status(state="stopped")

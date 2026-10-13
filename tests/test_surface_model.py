import json

import pytest

from exo.core.types import Message, ToolCall, ToolResult
from exo.protocol import schema as s

from exo_os.profile import Profile
from exo_os.surface.model import (NOT_RUNNING, RECENT_CHARS, RECENT_LINES, WAITING, WORKING, Line, Panel,
                                  SurfaceModel, approval_token, markdown_to_plain, match_apps, panel_view)

SID = "s_0000000000000001"


def _profile(plain=True):
    p = (Profile.default()
         .put_app({"name": "WhatsApp", "url": "https://web.whatsapp.com/", "key": "SUPER+1"})
         .put_app({"name": "Gmail", "url": "https://mail.google.com/", "key": "SUPER+2"})
         .put_app({"name": "Rhino CRM", "url": "http://127.0.0.1:8741/", "key": "SUPER+R"}))
    return p.set_value("surface.plain", plain)


def _model(plain=True):
    m = SurfaceModel(_profile(plain))
    m.set_session(SID)
    m.set_connected(True)
    return m


_seq = iter(range(1, 10_000))


def ev(body, session=SID):
    return s.Event.of(body, next(_seq), session)


def _assistant(text, mid="m1"):
    msg = Message.assistant(text)
    msg.id = mid
    return s.MessageCompleted(message=msg)


# -- streaming and messages


def test_deltas_stream_then_completed_message_becomes_one_line():
    m = _model()
    assert m.apply(ev(s.TurnStarted(turn_id="t1", model="provider:model-x"))) == {"status", "transcript"}
    assert m.busy and m.status == WORKING  # plain mode: no model name
    m.apply(ev(s.MessageDelta(message_id="m1", text="Call ")))
    m.apply(ev(s.MessageDelta(message_id="m1", text="Lead A.")))
    assert m.stream_text() == "Call Lead A."
    m.apply(ev(_assistant("Call Lead A.")))
    assert m.stream_text() == ""
    assert [(ln.role, ln.text) for ln in m.lines] == [("exo", "Call Lead A.")]
    m.apply(ev(s.TurnCompleted(turn_id="t1")))
    assert not m.busy and m.status == ""
    assert [ln.text for ln in m.visible_lines()] == ["Call Lead A."]


def test_streamed_text_without_completed_message_is_kept_at_turn_end():
    m = _model()
    m.apply(ev(s.TurnStarted(turn_id="t1")))
    m.apply(ev(s.MessageDelta(message_id="m9", text="half an answer")))
    m.apply(ev(s.TurnCompleted(turn_id="t1", stop_reason="interrupted")))
    assert [(ln.role, ln.text) for ln in m.lines] == [("exo", "half an answer"), ("note", "Stopped.")]


def test_operator_mode_shows_model_and_tools():
    m = _model(plain=False)
    m.apply(ev(s.TurnStarted(turn_id="t1", model="provider:model-x")))
    assert "provider:model-x" in m.status
    m.apply(ev(s.ToolStarted(call=ToolCall(id="c1", name="crm_search", arguments={"q": "today"}))))
    assert m.lines[-1].role == "tool" and "crm_search" in m.lines[-1].text
    m.apply(ev(s.ToolCompleted(result=ToolResult.of_text("c1", "boom", is_error=True), is_error=True)))
    assert "boom" in m.lines[-1].text
    m.apply(ev(s.Notice(text="cwd is /tmp", level="warn")))
    assert m.lines[-1].text == "cwd is /tmp"
    m.apply(ev(s.Error(code="provider", message="HTTP 529")))
    assert m.lines[-1].text == "Error provider: HTTP 529"


# -- plain mode hides the machinery


def test_plain_mode_hides_tools_notices_and_internals():
    m = _model()
    m.apply(ev(s.TurnStarted(turn_id="t1", model="provider:model-x")))
    for body in (s.ToolStarted(call=ToolCall(id="c1", name="crm_search", arguments={"q": "x"})),
                 s.ToolOutput(call_id="c1", text="rows..."),
                 s.ToolCompleted(result=ToolResult.of_text("c1", "Traceback ...", is_error=True), is_error=True),
                 s.Notice(text="cwd is /home/x", level="warn"),
                 s.ReasoningDelta(message_id="m1", text="thinking"),
                 s.TaskUpdated(task_id="k1", status="done", detail="done"),
                 s.UsageUpdated(usage=s.Usage(), model="provider:model-x"),
                 s.HarnessError(where="hook", message="KeyError")):
        m.apply(ev(body))
    assert m.lines == []
    assert m.status == WORKING
    m.apply(ev(s.Error(code="provider", message="HTTP 529 overloaded from provider:model-x")))
    assert m.lines[-1].text == "Something went wrong. Please try again."
    m.apply(ev(s.TurnCompleted(turn_id="t1", stop_reason="error")))
    assert m.lines[-1].text == "Something went wrong, so I stopped. Please try again."
    assert not any("model-x" in ln.text or "Traceback" in ln.text for ln in m.lines)


def test_plain_mode_has_no_slash_commands():
    m = _model()
    m.set_input("/trace")
    assert [x.kind for x in m.suggestions] == ["ask"]
    op = _model(plain=False)
    op.set_input("/trace last")
    act = op.submit()
    assert isinstance(act.command, s.CommandRun) and act.command.name == "trace" and act.command.args == "last"


def test_plain_command_errors_are_sentences():
    m = _model()
    m.command_failed("busy", "a turn is running in this thread; try again when it ends")
    assert m.lines[-1].text == "I am still working on the last thing. Please wait a moment."
    m.command_failed("weird_code", "x")
    assert m.lines[-1].text == "Something went wrong. Please try again."


def test_other_threads_events_are_ignored_but_global_ones_pass():
    m = _model(plain=False)
    m.apply(ev(s.MessageDelta(message_id="m1", text="not mine"), session="s_other"))
    assert m.stream_text() == ""
    m.apply(ev(s.Notice(text="for everyone"), session=None))
    assert m.lines[-1].text == "for everyone"


# -- approvals


def _ask(rid="a1", binding="h1", **kw):
    return s.ApprovalRequested(request_id=rid, summary="Send this message to Ahmed on WhatsApp?",
                               call=ToolCall(id="c1", name="whatsapp_send", arguments={"to": "Ahmed"}),
                               binding=binding, **kw)


def test_approval_requested_then_answered_echoes_binding():
    m = _model()
    assert m.apply(ev(_ask())) == {"approval", "status"}
    card = m.approval
    assert card.summary.startswith("Send this message") and card.binding == "h1"
    assert card.options == ["allow", "deny"]  # plain: no standing grant
    assert json.loads(card.detail.split("\n", 1)[1]) == {
        "approval_request_id": "a1", "tool": "whatsapp_send", "arguments": {"to": "Ahmed"}}
    assert m.status == WAITING
    assert m.answer("allow") is None  # arrival is only a pending notification
    m.show_approvals()
    resp = m.answer("allow")
    assert resp == s.ApprovalRespond(request_id="a1", decision="allow", client="exo-surface", binding="h1")
    assert m.approval is None
    # A replay after a sent answer must not re-open a card.
    m.apply(ev(_ask()))
    assert m.approval is None
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="allow")))
    assert m.approval is None


@pytest.mark.parametrize("plain", [True, False])
def test_generic_approval_shows_full_literal_request_with_visible_suffix(plain):
    m = _model(plain=plain)
    payload = "<b>literal</b>" + "long body " * 1200 + "\u202eTRAILING_DESTINATION"
    call = ToolCall(id="c_long", name="send_message", arguments={"target": "Ahmed", "message": payload})
    m.apply(ev(s.ApprovalRequested(request_id="a_long", summary="A short summary", call=call,
                                   binding="bound")))
    detail = m.approval.detail
    assert "TRAILING_DESTINATION" in detail
    assert "\\u202e" in detail and "\u202e" not in detail
    assert "<b>literal</b>" in detail  # displayed as text, never interpreted as markup
    assert json.loads(detail.split("\n", 1)[1])["arguments"]["message"] == payload


def test_approval_resolved_elsewhere_closes_the_card_and_queue_moves_on():
    m = _model(plain=False)
    m.apply(ev(_ask("a1")))
    m.apply(ev(_ask("a2", binding="h2"), session="s_scheduled"))  # any thread's approval reaches the person
    assert m.approval.request_id == "a1" and "whatsapp_send" in m.approval.detail
    assert "allow_always" in m.approval.options
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="deny", by="phone")))
    assert m.approval.request_id == "a2" and m.approval.session == "s_scheduled"
    assert m.reviewed_approval is None  # the next request needs its own review gesture
    m.show_approvals()
    assert m.answer("maybe") is None  # not an offered option
    assert m.answer("deny").decision == "deny"
    assert m.status == "Checking your answer…"


def test_approvals_for_a_seat_are_not_shown_and_list_rows_are_accepted():
    m = _model()
    m.apply(ev(_ask("a1", approvers=["seat:reviewer"])))
    assert m.approval is None
    m.add_approval({"request_id": "a3", "session": SID})  # an approvals.list row with no summary
    assert m.approval.summary == "Exo is waiting for your answer."
    m.add_approval({"request_id": "a4", "summary": "Pay $40?", "binding": "h4",
                    "call": {"id": "c", "name": "pay", "arguments": {}}})
    assert [a.request_id for a in m.approvals] == ["a3", "a4"]


# -- panels


def test_panel_open_update_close_and_responses():
    m = _model()
    m.apply(ev(s.UiPanel(panel_id="p1", action="open", kind="table", title="Today", text="3 calls",
                         data={"columns": ["Who", "Why"], "rows": [["Lead A", "bid due Thu"]]})))
    assert list(m.panels) == ["p1"]
    assert m.panel_shown("p1") == s.UiRespond(panel_id="p1", action="opened")
    assert m.panel_shown("p1") is None  # only once
    m.apply(ev(s.UiPanel(panel_id="p1", action="update", kind="table", text="4 calls",
                         data={"columns": ["Who"], "rows": [["Lead B"]]})))
    p = m.panels["p1"]
    assert p.title == "Today" and p.text == "4 calls" and p.data["rows"] == [["Lead B"]]
    assert m.close_panel("p1") == s.UiRespond(panel_id="p1", action="dismissed")
    assert m.panels == {}


def test_panel_closed_by_exo_before_seen_is_ignored():
    m = _model()
    m.apply(ev(s.UiPanel(panel_id="p2", action="open", text="hint")))
    assert m.apply(ev(s.UiPanel(panel_id="p2", action="close"))) == {"panels"}
    assert m.take_outbox() == [s.UiRespond(panel_id="p2", action="ignored")]
    assert m.take_outbox() == []
    m.apply(ev(s.UiPanel(panel_id="p3", action="open", text="seen")))
    m.panel_shown("p3")
    m.apply(ev(s.UiPanel(panel_id="p3", action="close")))
    assert m.take_outbox() == []


def test_choice_acts_and_sends_the_label():
    m = _model()
    m.apply(ev(s.UiPanel(panel_id="p4", action="open", kind="choice", text="Which one?",
                         data={"prompt": "Send which?", "options": [{"id": "a", "label": "Short"}, "Long"]})))
    out = m.choose("p4", "a")
    assert out[0] == s.UiRespond(panel_id="p4", action="acted", value={"choice": "a"})
    assert out[1] == s.TurnSend(session=SID, text="Short", mode="send")
    assert panel_view(m.panels["p4"])["options"] == [("a", "Short"), ("Long", "Long")]


def test_choice_while_disconnected_sends_no_message_and_says_so():
    m = _model()
    m.apply(ev(s.UiPanel(panel_id="p4", action="open", kind="choice", text="Which one?",
                         data={"options": [{"id": "a", "label": "Short"}]})))
    m.set_connected(False)
    # Only the learning signal goes out; the label is not sent as a message, and the person is told.
    assert m.choose("p4", "a") == [s.UiRespond(panel_id="p4", action="acted", value={"choice": "a"})]
    assert m.lines[-1] == Line("error", NOT_RUNNING)
    assert not any(line.role == "you" for line in m.lines)
    m.set_connected(True)
    m.session = None  # connected but no thread yet: the same
    m.lines.clear()
    assert [type(b) for b in m.choose("p4", "a")] == [s.UiRespond]
    assert m.lines[-1] == Line("error", NOT_RUNNING)


def test_panel_view_shapes():
    def view(kind, data, text="t"):
        return panel_view(Panel("p", kind, "T", text, data))

    assert view("table", {"rows": [{"a": 1, "b": None}]})["rows"] == [["1", ""]]
    assert view("tasks", {"tasks": [{"title": "Call", "status": "done"}, "Email"]})["tasks"] == [
        ("Call", "done"), ("Email", "queued")]
    assert view("chart", {"labels": ["Mon", "Tue"], "values": [3, "x"]})["bars"] == [("Mon", 3.0), ("Tue", 0.0)]
    assert view("diff", {"files": [{"path": "a", "diff": "-x\n+y"}]})["files"] == [("a", "-x\n+y")]
    assert view("file", {"path": "f.txt", "content": "hi"})["content"] == "hi"
    assert view("markdown", {}, text="# Hi\n- **one**")["body"] == "Hi\n• one"
    assert view("table", {"rows": 5})["text"] == "t"  # bad data falls back to the text form


def test_markdown_to_plain():
    md = "## Today\n* call `Lead A` about _the bid_\n```\ncode\n```\nsee [CRM](http://x)"
    assert markdown_to_plain(md) == "Today\n• call Lead A about the bid\ncode\nsee CRM (http://x)"


# -- palette


def test_match_apps_strong_and_weak():
    names = ["WhatsApp", "Gmail", "Rhino CRM", "Browser", "Terminal"]
    assert match_apps("whats", names) == (["WhatsApp"], [])
    assert match_apps("crm", names) == (["Rhino CRM"], [])
    assert match_apps("open gm", names) == (["Gmail"], [])
    assert match_apps("wapp", names) == ([], ["WhatsApp"])
    assert match_apps("hi", names) == ([], [])  # too short to guess at
    assert match_apps("who do I need to call today?", names) == ([], [])


def test_palette_enter_opens_an_app_or_asks_exo():
    m = _model()
    m.set_input("whats")
    assert [x.kind for x in m.suggestions] == ["app", "ask"]
    act = m.submit()
    assert (act.kind, act.value) == ("open_app", "WhatsApp")
    assert m.text == "" and m.suggestions == []

    m.set_input("wapp")  # weak match: shown second, Enter still asks
    assert [x.label for x in m.suggestions] == ["Ask Exo: wapp", "Open WhatsApp"]
    m.move(1)
    assert m.submit().value == "WhatsApp"

    m.set_input("who do I need to call today?")
    act = m.submit()
    assert act.kind == "send"
    assert act.command == s.TurnSend(session=SID, text="who do I need to call today?", mode="send")
    assert m.lines[-1].role == "you"


def test_palette_new_apps_follow_the_profile():
    m = _model()
    m.set_input("dri")
    assert m.suggestions[0].kind == "ask"
    m.set_profile(_profile().put_app({"name": "Drive", "url": "https://drive.google.com/"}))
    assert m.suggestions[0].label == "Open Drive"


def test_send_while_busy_queues_and_offline_says_so():
    m = _model()
    m.apply(ev(s.TurnStarted(turn_id="t1")))
    m.set_input("and Lead B?")
    assert m.submit().command.mode == "queue"
    m.set_connected(False)
    assert m.status == NOT_RUNNING and not m.busy
    m.set_input("whats")
    assert m.submit().kind == "open_app"  # apps still open with exod down
    m.set_input("hello there")
    assert m.submit().kind == "none"
    assert m.lines[-1].text == NOT_RUNNING


def test_question_is_answered_from_the_palette():
    m = _model()
    m.apply(ev(s.QuestionAsked(question_id="q1", text="Which Ahmed?", options=["Ahmed K", "Ahmed R"])))
    assert m.status == WAITING
    m.set_input("Ahmed K")
    assert m.suggestions[-1].label == "Answer: Ahmed K"
    act = m.submit()
    assert act.command == s.QuestionAnswer(question_id="q1", answer="Ahmed K")
    assert m.question is None


@pytest.mark.parametrize("word,kind", [("transcript", "show_transcript"), ("panels", "show_panels")])
def test_local_reading_actions_are_explicit_and_question_answers_remain_default(word, kind):
    m = _model()
    m.set_input(word)
    assert m.suggestions[0].kind == word
    assert m.submit().kind == kind
    assert not m.lines  # viewing local state is not a message or model turn

    m.apply(ev(s.QuestionAsked(question_id="q_local", text="What should I call it?")))
    m.set_input(word)
    assert [item.kind for item in m.suggestions] == ["ask", word]
    assert m.submit().command == s.QuestionAnswer(question_id="q_local", answer=word)
    m.apply(ev(s.QuestionAsked(question_id="q_explicit", text="And now?")))
    m.set_input(word)
    m.move(1)
    assert m.submit().kind == kind
    assert m.question.question_id == "q_explicit"


def test_recent_conversation_reads_only_loaded_lines_and_keeps_newest_when_bounded():
    m = _model()
    m.lines = [Line("you", "first loaded line")] + [Line("exo", f"reply {n}") for n in range(RECENT_LINES)]
    m.streaming["m_live"] = "newest streamed answer"
    view = m.recent_conversation()
    assert "Only currently loaded history" in view
    assert "Earlier loaded content omitted" in view
    assert "first loaded line" not in view
    assert "newest streamed answer" in view
    assert len(view.splitlines()) <= RECENT_LINES + 3
    assert "SessionGet" not in view

    m.lines = [Line("exo", "old marker"), Line("exo", "tail marker" + "x" * (RECENT_CHARS + 10))]
    m.streaming.clear()
    view = m.recent_conversation()
    assert "old marker" not in view and "Earlier loaded content omitted" in view
    assert view.endswith("x" * 100)
    assert len(view) < RECENT_CHARS + 300  # bounded view including its explicit scope/limit notice


def test_transcript_collapses_to_last_exchange_and_clears_when_hidden():
    m = _model()
    for i in range(2):
        m.set_input(f"question {i}")
        m.submit()
        m.apply(ev(s.TurnStarted(turn_id=f"t{i}")))
        m.apply(ev(s.MessageDelta(message_id=f"m{i}", text=f"answer {i}")))
        if i == 1:
            assert [ln.text for ln in m.visible_lines()][-1] == "question 1"  # busy: recent lines
            assert m.stream_text() == "answer 1"
        m.apply(ev(_assistant(f"answer {i}", f"m{i}")))
        m.apply(ev(s.TurnCompleted(turn_id=f"t{i}")))
    assert [ln.text for ln in m.visible_lines()] == ["question 1", "answer 1"]
    m.clear_transcript()
    assert m.visible_lines() == []
    assert len(m.lines) == 4


# -- recovery: a send is never a resolution


def test_uncertain_answer_rearms_only_after_pending_snapshot():
    m = _model()
    m.apply(ev(_ask()))
    m.show_approvals()
    assert m.answer("allow") is not None
    assert m.answer("allow") is None  # duplicate tap never sends the same request twice
    m.apply(ev(_ask()))  # replay while sending stays suppressed
    m.reconcile_approvals([{"request_id": "a1"}], 50000)
    assert m.approval is None  # a snapshot while still in flight must not rearm
    m.approval_result("a1")
    m.reconcile_approvals([{"request_id": "a1"}], 50001)
    assert m.approval.binding == "h1"  # minimal list rows retain the text and binding actually shown
    assert m.reviewed_approval is None and m.answer("deny") is None
    m.show_approvals()
    assert m.answer("deny").binding == "h1"  # a fresh human answer, never an automatic resend


def test_lost_reply_but_resolved_snapshot_does_not_reopen_request():
    m = _model()
    requested = ev(_ask())
    m.apply(requested)
    m.show_approvals()
    m.answer("allow")
    m.approval_result("a1")
    m.reconcile_approvals([], requested.seq)
    assert not m.answering and m.approval is None
    m.apply(requested)  # historical requested replay is older than the authoritative snapshot
    assert m.approval is None


def test_empty_snapshot_removes_missed_resolutions_and_restart_state():
    m = _model()
    m.apply(ev(_ask()))
    m.set_connected(False)
    m.reconcile_approvals([], 0)  # restarted exod has a new, lower sequence head
    m.set_connected(True)
    assert m.approval is None and m.status == ""


def test_snapshot_retains_newer_event_until_resolution_replay():
    m = _model()
    requested = ev(_ask())
    m.apply(requested)
    m.reconcile_approvals([], requested.seq - 1)
    assert m.approval.request_id == "a1"
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="deny")))
    assert m.approval is None


def test_old_snapshot_cannot_replace_newer_same_id_content_or_generation():
    from exo_os.surface.model import approval_token
    m = _model(plain=False)
    requested = ev(s.ApprovalRequested(request_id="a1", summary="NEW", binding="new",
        options=["deny"], call=ToolCall(id="new_call", name="send_message", arguments={"message": "new body"})))
    m.apply(requested)
    m.show_approvals()
    original = approval_token(m.reviewed_approval)
    for row in (
        {"request_id": "a1", "summary": "OLD", "binding": "old", "options": ["allow", "deny"],
         "call": {"id": "old_call", "name": "send_message", "arguments": {"message": "old body"}}},
        {"request_id": "a1", "summary": "STALE WITHOUT CALL", "binding": "older"},
    ):
        m.reconcile_approvals([row], requested.seq - 1)
        assert approval_token(m.approval) == original
        assert m.approval_seq["a1"] == requested.seq
        assert m.reviewed_approval is None and m.answer("deny") is None
        assert m.approval.summary == "NEW" and "new body" in m.approval.detail
        assert m.approval.binding == "new" and m.approval.options == ["deny"]


def test_escape_defers_without_answer_and_only_explicit_review_returns_card():
    m = _model()
    m.apply(ev(_ask()))
    m.show_approvals()
    m.defer_approval()
    assert m.approval is None and len(m.approvals) == 1 and not m.answering
    m.reconcile_approvals([{"request_id": "a1"}], 50000)
    assert m.approval is None  # incidental render or reconnect does not steal focus back
    m.show_approvals()
    assert m.approval.request_id == "a1" and m.approval.binding == "h1"
    assert m.reviewed_approval is m.approval
    m.set_connected(False)
    assert m.answer("allow") is None


def test_stale_displayed_click_cannot_answer_the_next_request():
    from exo_os.surface.model import approval_token
    m = _model()
    m.apply(ev(_ask("a1")))
    old = approval_token(m.approval)
    m.apply(ev(_ask("a2")))
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="deny")))
    assert m.answer("allow", old, explicit_click=True) is None
    current = approval_token(m.approval)
    assert m.answer("deny", current, explicit_click=True).request_id == "a2"
    assert m.answering["a2"].binding == "h1"


def test_retained_callback_cannot_answer_recreated_request_with_identical_content():
    from exo_os.surface.model import approval_token
    m = _model()
    m.apply(ev(_ask(binding="")))
    token = approval_token(m.approval)
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="deny")))
    m.apply(ev(_ask(binding="")))
    assert m.answer("allow", token, explicit_click=True) is None
    m.show_approvals()
    assert m.answer("allow", token) is None
    assert m.answer("deny").request_id == "a1"


@pytest.mark.parametrize("change", ["binding", "summary", "detail", "options"])
def test_replacement_of_unbound_or_bound_request_revokes_review_and_old_click(change):
    from exo_os.surface.model import approval_token
    m = _model(plain=False)
    request = {"request_id": "a1", "binding": "", "summary": "Original", "options": ["allow", "deny"],
               "call": {"id": "c1", "name": "send_message", "arguments": {"message": "old"}}}
    m.add_approval(request)
    m.show_approvals()
    old = approval_token(m.reviewed_approval)
    changed = dict(request)
    if change == "detail":
        changed["call"] = {"id": "c1", "name": "send_message", "arguments": {"message": "new"}}
    else:
        changed[change] = ["deny"] if change == "options" else "new"
    m.add_approval(changed)
    assert m.reviewed_approval is None
    assert m.answer("deny", old, explicit_click=True) is None
    assert m.answer("deny") is None
    m.show_approvals()
    assert m.answer("deny").request_id == "a1"


def test_review_content_is_detached_from_mutable_input_and_invalidated_by_live_mutation():
    from exo_os.surface.model import approval_token
    m = _model(plain=False)
    options = ["allow", "deny"]
    args = {"message": "original"}
    m.add_approval({"request_id": "a1", "options": options,
                    "call": {"id": "c", "name": "send_message", "arguments": args}})
    m.show_approvals()
    token = approval_token(m.reviewed_approval)
    options.clear(); args["message"] = "changed"
    assert m.reviewed_approval is not None and "original" in m.reviewed_approval.detail
    m.approval.options.remove("allow")
    assert m.reviewed_approval is None
    assert m.answer("deny", token, explicit_click=True) is None


@pytest.mark.parametrize("reset", ["snapshot", "disconnect", "reconnect", "prompt", "uncertain"])
def test_review_requires_fresh_gesture_after_state_reset(reset):
    m = _model()
    m.apply(ev(_ask()))
    m.show_approvals()
    if reset == "snapshot":
        m.reconcile_approvals([{"request_id": "a1"}], 50000)
    elif reset == "prompt":
        m.leave_approval_review()
    elif reset == "uncertain":
        m.approval_result("a1")
    else:
        m.set_connected(False)
        if reset == "reconnect":
            m.set_connected(True)
    assert m.reviewed_approval is None and m.answer("allow") is None
    assert len(m.approvals) == 1 and not m.answering


@pytest.mark.parametrize("key, action", [("Escape", "defer"), ("y", "allow"), ("N", "deny"), ("Return", None)])
def test_card_key_mapping_never_turns_escape_into_a_decision(key, action):
    from exo_os.surface.model import approval_key
    assert approval_key(key) == action


def _install_row(rid, names):
    return {"request_id": rid, "summary": "system_package_install (costly, private). Why: x. Default if no reply: skip.",
            "risk": "high", "options": ["allow", "deny", "allow_always"], "approvers": ["principal"],
            "binding": f"b_{rid}", "call": {"id": f"c_{rid}", "name": "system_package_install",
                                            "arguments": {"names": names, "reason": "x"}}}


@pytest.mark.parametrize("bad_names", [[1, 2], "git", [], None])
def test_malformed_arguments_get_a_refuse_only_card(bad_names):
    m = _model(plain=True)
    m.add_approval(_install_row("bad", bad_names))
    card = m.approval
    assert card.request_id == "bad" and card.options == ["deny"]
    assert "can only be refused" in card.summary and "Install g, i, t" not in card.summary
    assert "system_package_install" in card.detail  # the exact request stays inspectable
    assert m.answer("allow", approval_token(card), explicit_click=True) is None
    reply = m.answer("deny", approval_token(card), explicit_click=True)
    assert reply is not None and reply.decision == "deny" and reply.binding == "b_bad"


@pytest.mark.parametrize("bad_first", [True, False])
def test_one_malformed_row_does_not_hide_the_other_approvals(bad_first):
    m = _model(plain=True)
    rows = [_install_row("bad", [1, 2]), _install_row("good", ["git"])]
    m.reconcile_approvals(rows if bad_first else rows[::-1], 5)
    shown = {a.request_id: a for a in m.approvals}
    assert set(shown) == {"bad", "good"}
    assert shown["good"].summary == "Install git on this computer? Reason: x. It can be undone."
    assert shown["good"].options == ["allow", "deny"]
    assert shown["bad"].options == ["deny"]


def test_a_row_that_fails_for_any_reason_still_shows_a_refuse_only_card(monkeypatch):
    m = _model(plain=True)

    def boom(*_a, **_k):
        raise RuntimeError("wording failed")

    monkeypatch.setattr("exo_os.surface.model.plain_wording", boom)
    m.reconcile_approvals([_install_row("bad", ["git"]), {"request_id": "plain", "summary": "Pay $40?"}], 5)
    cards = {a.request_id: a for a in m.approvals}
    assert set(cards) == {"bad", "plain"} and cards["bad"].options == ["deny"]
    assert cards["bad"].binding == "b_bad"
    assert cards["plain"].options == ["allow", "deny"]


def test_malformed_credential_request_is_refuse_only():
    m = _model(plain=True)
    m.add_approval({"request_id": "cred", "summary": "x", "binding": "b", "options": ["allow", "deny"],
                    "call": {"id": "c", "name": "credential_request", "arguments": ["not", "a", "dict"]}})
    assert m.approval.options == ["deny"]


def test_malformed_arguments_in_full_mode_do_not_crash():
    m = _model(plain=False)
    m.add_approval(_install_row("bad", [1, 2]))
    assert m.approval.request_id == "bad"

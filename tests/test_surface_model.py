from exo.core.types import Message, ToolCall, ToolResult
from exo.protocol import schema as s

from exo_os.profile import Profile
from exo_os.surface.model import (NOT_RUNNING, WAITING, WORKING, Panel, SurfaceModel, markdown_to_plain,
                                  match_apps, panel_view)

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
    assert m.apply(ev(s.TurnStarted(turn_id="t1", model="anthropic:claude-x"))) == {"status", "transcript"}
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
    m.apply(ev(s.TurnStarted(turn_id="t1", model="anthropic:claude-x")))
    assert "anthropic:claude-x" in m.status
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
    m.apply(ev(s.TurnStarted(turn_id="t1", model="anthropic:claude-x")))
    for body in (s.ToolStarted(call=ToolCall(id="c1", name="crm_search", arguments={"q": "x"})),
                 s.ToolOutput(call_id="c1", text="rows..."),
                 s.ToolCompleted(result=ToolResult.of_text("c1", "Traceback ...", is_error=True), is_error=True),
                 s.Notice(text="cwd is /home/x", level="warn"),
                 s.ReasoningDelta(message_id="m1", text="thinking"),
                 s.TaskUpdated(task_id="k1", status="done", detail="done"),
                 s.UsageUpdated(usage=s.Usage(), model="anthropic:claude-x"),
                 s.HarnessError(where="hook", message="KeyError")):
        m.apply(ev(body))
    assert m.lines == []
    assert m.status == WORKING
    m.apply(ev(s.Error(code="provider", message="HTTP 529 overloaded from anthropic:claude-x")))
    assert m.lines[-1].text == "Something went wrong. Please try again."
    m.apply(ev(s.TurnCompleted(turn_id="t1", stop_reason="error")))
    assert m.lines[-1].text == "Something went wrong, so I stopped. Please try again."
    assert not any("claude" in ln.text or "Traceback" in ln.text for ln in m.lines)


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
    assert card.detail == ""  # plain: no tool internals
    assert m.status == WAITING
    resp = m.answer("allow")
    assert resp == s.ApprovalRespond(request_id="a1", decision="allow", client="exo-surface", binding="h1")
    assert m.approval is None
    # the resolved event that follows changes nothing visible, and a replay of the request does not re-open it
    m.apply(ev(_ask()))
    assert m.approval is None
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="allow")))
    assert m.approval is None


def test_approval_resolved_elsewhere_closes_the_card_and_queue_moves_on():
    m = _model(plain=False)
    m.apply(ev(_ask("a1")))
    m.apply(ev(_ask("a2", binding="h2"), session="s_scheduled"))  # any thread's approval reaches the person
    assert m.approval.request_id == "a1" and "whatsapp_send" in m.approval.detail
    assert "allow_always" in m.approval.options
    m.apply(ev(s.ApprovalResolved(request_id="a1", decision="deny", by="phone")))
    assert m.approval.request_id == "a2" and m.approval.session == "s_scheduled"
    assert m.answer("maybe") is None  # not an offered option
    assert m.answer("deny").decision == "deny"
    assert m.status == ""


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

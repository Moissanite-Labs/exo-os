"""What the surface shows, worked out from Exo protocol events and what the person types.

No GTK here. The view asks this model for state and draws it; the connection feeds it events. Keeping the
rules in plain Python means every one of them (what plain mode hides, how an approval is answered, which
app "whats" means) is unit tested without a display.

Plain mode (profile `surface.plain`, on by default) is for a person who is not technical (DESIGN.md §9.3,
point 8): no slash commands, no model or tool names, tool activity is one calm "Working…" line, and errors
are plain sentences. Power stays reachable for an operator by turning plain mode off.

Methods that need something sent to exod return protocol command bodies (`exo.protocol.schema`); the app
hands them to the connection. The model never talks to the network itself.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from itertools import chain
from typing import Any

from exo.protocol import schema as s

from ..words import approval_request_details, credential_request_preview, plain_summary, plain_wording
from ..profile import Profile

CLIENT = "exo-surface"
WORKING = "Working…"
NOT_RUNNING = "Exo is not running right now."
WAITING = "Waiting for your answer."
# The view shows at most this many transcript lines while a turn runs.
TRANSCRIPT_LINES = 12
MAX_SUGGESTIONS = 5
RECENT_LINES = 200
RECENT_CHARS = 100_000

# Plain sentences for command errors and error events, by code (codes from exod's CommandFailed and Error).
PLAIN_ERRORS = {
    "busy": "I am still working on the last thing. Please wait a moment.",
    "stale": "That request changed after it was shown, so I did not use your answer. Please look again.",
    "not_found": "That is no longer waiting for an answer.",
    "forbidden": "You cannot answer that one here.",
    "session_locked": "This conversation is closed. Close the bar and open it again to start a new one.",
    "not_connected": NOT_RUNNING,
    "model_spend_refused": "This request cannot run under the current spending policy. Type budget to inspect the limits, or ask an administrator to review the model configuration.",
    "model_spend_stopped": "Model work is stopped. Type budget to inspect spending and limits, then ask an administrator to review them.",
}
PLAIN_ERROR = "Something went wrong. Please try again."
PLAIN_STOPS = {
    "interrupted": "Stopped.",
    "max_tokens": "I stopped before finishing. Ask me to go on if you want the rest.",
    "error": "Something went wrong, so I stopped. Please try again.",
}


def approval_key(name: str) -> str | None:
    """Card keyboard actions. Dismiss gestures never become an approval decision."""
    return {"y": "allow", "n": "deny", "escape": "defer"}.get(name.lower())


@dataclass
class Line:
    """One line of the transcript. role: you, exo, note (calm status), error, tool (plain mode never)."""

    role: str
    text: str


@dataclass
class ApprovalCard:
    request_id: str
    summary: str
    binding: str = ""
    options: list[str] = field(default_factory=lambda: ["allow", "deny"])
    risk: str = "medium"
    session: str | None = None
    detail: str = ""  # full literal request in both modes when a tool call is available
    generation: object = field(default_factory=object, compare=False, repr=False)


def approval_token(card: ApprovalCard) -> tuple[Any, ...]:
    """Detached displayed content plus local request generation, including unbound requests."""
    return (card.generation, card.request_id, card.binding, card.summary, card.detail,
            tuple(card.options), card.risk, card.session)


@dataclass
class Panel:
    panel_id: str
    kind: str = "markdown"
    title: str = ""
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    shown: bool = False  # the person has seen it (drives opened versus ignored)
    chosen: str | None = None  # choice panel: the option already taken; a repeat click is a no-op


@dataclass
class Suggestion:
    """A palette row. kind: app (open it), ask (send the text to Exo), command (operator only)."""

    kind: str
    label: str
    value: str
    strong: bool = False  # Enter picks it without arrowing to it


@dataclass
class Action:
    """What Enter in the palette asks the app to do."""

    kind: str  # open_app, send, show_transcript, show_panels, none
    value: str = ""
    command: Any = None  # protocol body for kind send


class SurfaceModel:
    def __init__(self, profile: Profile | None = None, plain: bool | None = None) -> None:
        self.profile = profile or Profile.default()
        self.plain = bool(self.profile.get("surface.plain")) if plain is None else plain
        self.session: str | None = None
        self.connected = False
        self.lines: list[Line] = []
        self.streaming: dict[str, str] = {}  # message_id -> text so far
        self.busy = False
        self._spend_error = False
        self.shown_from = 0  # transcript lines before this were cleared from view when the bar hid
        self.outbox: list[Any] = []  # commands an event calls for (a panel closed unseen is "ignored")
        self.activity = ""  # what the running turn is doing, for the status line
        self.approvals: list[ApprovalCard] = []
        self.answering: dict[str, ApprovalCard] = {}  # hidden until the authoritative pending list arrives
        self.answer_check: set[str] = set()  # delivery finished or uncertain; safe to reconcile
        self.approval_seq: dict[str, int] = {}
        self.approval_floor = 0  # a snapshot supersedes approval.requested replay through this seq
        self.deferred: set[str] = set()  # Esc: stays pending until explicitly reviewed again
        self._reviewed_token: tuple[Any, ...] | None = None
        self.panels: dict[str, Panel] = {}
        self.question: s.QuestionAsked | None = None
        self.text = ""
        self.suggestions: list[Suggestion] = []
        self.selected = 0

    # -- profile and connection

    def set_profile(self, profile: Profile) -> set[str]:
        """A new machine profile (apps may have changed). Plain mode follows it too."""
        self.profile = profile
        self.plain = bool(profile.get("surface.plain"))
        return self.set_input(self.text)

    def set_connected(self, up: bool) -> set[str]:
        self._reviewed_token = None
        self.connected = up
        if not up:
            self.busy = False
            self.streaming.clear()
            self.approval_seq.clear()  # a restarted daemon may reuse lower sequence numbers
        return {"status", "transcript", "approval"}

    def set_session(self, session: str) -> None:
        self.session = session

    # -- events

    def apply(self, ev: s.Event) -> set[str]:
        """Take one event; return which parts of the view changed (transcript, status, approval, panels)."""
        b = ev.body
        if isinstance(b, s.ApprovalRequested):
            if ev.seq <= self.approval_floor:
                return set()
            changes = self._approval_requested(b, ev.session)
            if changes:
                self.approval_seq[b.request_id] = ev.seq
            return changes
        if isinstance(b, s.ApprovalResolved):
            return self._approval_resolved(b.request_id)
        if ev.session is not None and self.session is not None and ev.session != self.session:
            return set()  # another thread's turn; only its approvals reach this person
        handler = getattr(self, "_on_" + type(b).__name__, None) if not isinstance(b, dict) else None
        return handler(b) if handler else set()

    def _on_TurnStarted(self, b: s.TurnStarted) -> set[str]:
        self.busy = True
        self._spend_error = False
        self.activity = WORKING if self.plain or not b.model else f"{WORKING} ({b.model})"
        return {"status", "transcript"}

    def _on_MessageDelta(self, b: s.MessageDelta) -> set[str]:
        self.streaming[b.message_id] = self.streaming.get(b.message_id, "") + b.text
        return {"transcript"}

    def _on_MessageCompleted(self, b: s.MessageCompleted) -> set[str]:
        msg = b.message
        text = (msg.text() if msg.role == "assistant" else "").strip()
        if msg.id:
            streamed = self.streaming.pop(msg.id, "")
        else:  # no id to match: the text streamed so far is this message
            streamed = "".join(self.streaming.values())
            self.streaming.clear()
        text = text or streamed.strip()
        if text:
            self.lines.append(Line("exo", text))
        return {"transcript"}

    def _on_ToolStarted(self, b: s.ToolStarted) -> set[str]:
        if self.plain:
            return set()  # the status already says Working…
        self.lines.append(Line("tool", f"{b.call.name} {_short_args(b.call.arguments)}".strip()))
        self.activity = f"Running {b.call.name}…"
        return {"status", "transcript"}

    def _on_ToolCompleted(self, b: s.ToolCompleted) -> set[str]:
        if self.plain or not (b.is_error or b.result.is_error):
            return set()
        self.lines.append(Line("tool", f"failed: {b.result.text()[:200]}"))
        return {"transcript"}

    def _on_TurnCompleted(self, b: s.TurnCompleted) -> set[str]:
        for text in self.streaming.values():  # text that never got its message.completed
            if text.strip():
                self.lines.append(Line("exo", text.strip()))
        self.streaming.clear()
        self.busy = False
        if b.stop_reason not in ("end", "tool_use") and not (b.stop_reason == "error" and self._spend_error):
            if self.plain:
                self.lines.append(Line("note", PLAIN_STOPS.get(b.stop_reason, PLAIN_STOPS["error"])))
            else:
                self.lines.append(Line("note", f"({b.stop_reason})"))
        return {"status", "transcript"}

    def _on_QuestionAsked(self, b: s.QuestionAsked) -> set[str]:
        self.question = b
        text = b.text + (f" ({' / '.join(b.options)})" if b.options else "")
        self.lines.append(Line("exo", text))
        return {"status", "transcript"}

    def _on_TaskUpdated(self, b: s.TaskUpdated) -> set[str]:
        if self.plain:
            return set()
        if b.status in ("done", "failed", "cancelled"):
            first = (b.detail or b.title).splitlines()[0] if (b.detail or b.title) else b.task_id
            self.lines.append(Line("note", f"Task {b.status}: {first}"))
            return {"transcript"}
        return set()

    def _on_Notice(self, b: s.Notice) -> set[str]:
        if self.plain:  # notices are about paths, routes and settings; an operator reads them
            return set()
        self.lines.append(Line("note", b.text))
        return {"transcript"}

    def _on_Error(self, b: s.Error) -> set[str]:
        if b.code in {"model_spend_refused", "model_spend_stopped"}:
            self._spend_error = True
        self.lines.append(Line("error", self.error_text(b.code, b.message)))
        return {"transcript"}

    def _on_UiPanel(self, b: s.UiPanel) -> set[str]:
        if b.action == "close":
            p = self.panels.pop(b.panel_id, None)
            if p is not None and not p.shown:
                self.outbox.append(s.UiRespond(panel_id=b.panel_id, action="ignored"))
            return {"panels"}
        old = self.panels.get(b.panel_id)
        if b.action == "update" and old is not None:
            old.kind = b.kind or old.kind
            old.title = b.title or old.title
            old.text = b.text or old.text
            if b.data:
                old.chosen = None  # new options are a new question
            old.data = b.data or old.data
            old.reason = b.reason or old.reason
        else:
            self.panels[b.panel_id] = Panel(b.panel_id, b.kind, b.title, b.text, dict(b.data), b.reason)
        return {"panels"}

    # -- approvals

    def _approval_requested(self, b: s.ApprovalRequested, session: str | None) -> set[str]:
        return self.add_approval({"request_id": b.request_id, "summary": b.summary, "risk": b.risk,
                                  "options": b.options, "approvers": b.approvers, "binding": b.binding,
                                  "call": b.call, "session": session})

    def add_approval(self, req: dict[str, Any]) -> set[str]:
        """An approval from an event or from `approvals.list` (which a late client reads on connect)."""
        rid = req.get("request_id") or ""
        approvers = req.get("approvers") or []
        if not rid or rid in self.answering:
            return set()
        if approvers and "principal" not in approvers:
            return set()  # a seat answers this one, not the person at the desk
        try:
            card = self._card_for(req, rid)
        except Exception:
            # One request that cannot be read or worded must not stop the others being shown. Its card can only refuse.
            card = self._unreadable_card(req, rid)
        previous = next((a for a in self.approvals if a.request_id == rid), None)
        if previous is not None:
            if previous == card:
                return set()
            self.approvals[self.approvals.index(previous)] = card
            self._reviewed_token = None
        else:
            self.approvals.append(card)
        return {"approval", "status"}

    def _card_for(self, req: dict[str, Any], rid: str) -> ApprovalCard:
        call = req.get("call")
        if isinstance(call, dict):
            call = s.ToolCall(id=call.get("id", ""), name=call.get("name", ""), arguments=call.get("arguments") or {})
        detail = approval_request_details(call.name, call.arguments, rid) if call is not None else ""
        options = [o for o in (req.get("options") or ["allow", "deny"]) if o in ("allow", "deny", "allow_always")]
        if self.plain:
            options = [o for o in options if o != "allow_always"]  # a standing grant is an operator's call
        summary = req.get("summary") or "Exo is waiting for your answer."
        if call is not None and call.name == "credential_request":
            summary, detail, permitted = credential_request_preview(call.arguments, rid)
            options = [o for o in options if o != "allow_always"]  # credentials are always one-shot
            if not permitted:
                options = ["deny"]  # no approval when the destination cannot be verified
        elif self.plain:
            words, malformed = plain_wording(call.name, call.arguments) if call else (None, False)
            summary = words or plain_summary(summary)
            if malformed:
                # Exo asks before it checks arguments. A request that cannot be worded truthfully can only be refused.
                summary = "This request is not well formed, so it can only be refused. " + summary
                options = ["deny"]
        return ApprovalCard(rid, summary, req.get("binding") or "", options or ["allow", "deny"],
                            req.get("risk") or "medium", req.get("session"), detail)

    def _unreadable_card(self, req: dict[str, Any], rid: str) -> ApprovalCard:
        binding = req.get("binding")
        session = req.get("session")
        return ApprovalCard(rid, "This request could not be read, so it can only be refused.",
                            binding if isinstance(binding, str) else "", ["deny"], "high",
                            session if isinstance(session, str) else None, "")

    def _approval_resolved(self, rid: str) -> set[str]:
        if self._reviewed_token is not None and self._reviewed_token[1] == rid:
            self._reviewed_token = None
        before = len(self.approvals)
        self.approvals = [a for a in self.approvals if a.request_id != rid]
        self.answering.pop(rid, None)
        self.answer_check.discard(rid)
        self.deferred.discard(rid)
        self.approval_seq.pop(rid, None)
        return {"approval", "status"} if len(self.approvals) != before else {"status"}

    @property
    def approval(self) -> ApprovalCard | None:
        """The oldest pending approval that has not been deferred."""
        return next((a for a in self.approvals if a.request_id not in self.deferred), None)

    @property
    def reviewed_approval(self) -> ApprovalCard | None:
        """Only the exact request snapshot the person explicitly opened can receive an answer."""
        if not self.connected:
            return None
        return next((a for a in self.approvals if approval_token(a) == self._reviewed_token), None)

    def answer(self, decision: str, token: tuple[Any, ...] | None = None,
               *, explicit_click: bool = False) -> s.ApprovalRespond | None:
        """The person answered the card. The binding is echoed back so an edited request cannot pass on an
        answer given to an older text."""
        card = self.approval if explicit_click else self.reviewed_approval
        if not self.connected or card is None or decision not in card.options:
            return None
        if token is not None and approval_token(card) != token:
            return None
        if explicit_click and token is None:
            return None  # an untargeted click cannot authorize whichever request is now first
        self.approvals.remove(card)
        self._reviewed_token = None
        self.answering[card.request_id] = card
        return s.ApprovalRespond(request_id=card.request_id, decision=decision, client=CLIENT, binding=card.binding)

    def defer_approval(self) -> set[str]:
        """Esc dismisses the visible card without answering or removing the open request."""
        if self.reviewed_approval is not None:
            self.deferred.add(self.reviewed_approval.request_id)
        self._reviewed_token = None
        return {"approval", "status"}

    def show_approvals(self) -> set[str]:
        """Explicitly review one request, including a previously deferred request."""
        if self.connected and self.approvals:
            card = self.approval or self.approvals[0]
            self.deferred.discard(card.request_id)
            self._reviewed_token = approval_token(card)
        return {"approval", "status"}

    def leave_approval_review(self) -> set[str]:
        self._reviewed_token = None
        return {"approval", "status"}

    def approval_result(self, request_id: str) -> set[str]:
        """Delivery finished (possibly with a lost reply). Only a pending snapshot permits another answer."""
        self._reviewed_token = None
        if request_id in self.answering:
            self.answer_check.add(request_id)
        return {"approval", "status"}

    def reconcile_approvals(self, rows: list[dict[str, Any]], through_seq: int) -> set[str]:
        """Replace stale state, rearm uncertain answers still pending, and suppress older request replay.

        The connection reads hello *before* approvals.list. Newer live events are retained until their
        resolutions arrive; the list has no atomic event cursor in the current core protocol.
        """
        old = {a.request_id: a for a in self.approvals} | self.answering
        self._reviewed_token = None  # a snapshot is not another human review gesture
        self.approval_floor = through_seq
        newer = [a for a in self.approvals if self.approval_seq.get(a.request_id, 0) > through_seq]
        newer_ids = {a.request_id for a in newer}
        self.approvals = newer
        for rid in self.answer_check:
            self.answering.pop(rid, None)
        self.answer_check.clear()
        for row in rows:
            rid = row.get("request_id")
            if rid in newer_ids:
                continue  # this older snapshot cannot replace a newer event or restore stale details
            previous = old.get(rid)
            if previous is not None:
                row = {"summary": previous.summary, "binding": previous.binding,
                       "options": previous.options, "risk": previous.risk, "session": previous.session, **row}
            self.add_approval(row)
            if previous is not None and "call" not in row:
                card = next((a for a in self.approvals if a.request_id == rid), None)
                if card is not None:
                    card.detail = previous.detail
        open_ids = {a.request_id for a in self.approvals} | set(self.answering)
        self.deferred.intersection_update(open_ids)
        self.approval_seq = {rid: seq for rid, seq in self.approval_seq.items() if rid in open_ids}
        return {"approval", "status"}

    # -- panels

    def panel_shown(self, panel_id: str) -> s.UiRespond | None:
        p = self.panels.get(panel_id)
        if p is None or p.shown:
            return None
        p.shown = True
        return s.UiRespond(panel_id=panel_id, action="opened")

    def close_panel(self, panel_id: str) -> s.UiRespond | None:
        """The person closed a panel: dismissed if they saw it, ignored if it went unseen."""
        p = self.panels.pop(panel_id, None)
        if p is None:
            return None
        return s.UiRespond(panel_id=panel_id, action="dismissed" if p.shown else "ignored")

    def choose(self, panel_id: str, option_id: str) -> list[Any]:
        """A choice button. ui.respond only teaches the learning loop; the core does not hand it to the
        running model, so the chosen label is also sent as the person's next message."""
        p = self.panels.get(panel_id)
        if p is None or p.chosen is not None:
            return []
        out: list[Any] = [s.UiRespond(panel_id=panel_id, action="acted", value={"choice": option_id})]
        label = next((lab for oid, lab in panel_view(p)["options"] if oid == option_id), option_id)
        send = self._turn(label)
        if send is not None:
            out.append(send)
            p.chosen = option_id  # a failed send leaves the choice open to try again
        return out

    # -- palette

    def set_input(self, text: str) -> set[str]:
        self.text = text
        self.suggestions = self._suggest(text)
        self.selected = 0
        return {"palette"}

    def move(self, step: int) -> set[str]:
        if self.suggestions:
            self.selected = (self.selected + step) % len(self.suggestions)
        return {"palette"}

    def _suggest(self, text: str) -> list[Suggestion]:
        q = text.strip()
        if not q:
            return []
        for names, kind, label in (("transcript", "transcript", "Read recent conversation"),
                                   ("panels", "panels", "Open panels")):
            if q.casefold() == names:
                action = Suggestion(kind, label, "", strong=self.question is None)
                answer = Suggestion("ask", ("Answer: " if self.question else "Ask Exo: ") + q, q)
                return [answer, action] if self.question else [action, answer]
        if self.question is None and q.casefold() in ("bud", "budget", "spend", "spending", "model spending", "model budget"):
            return [Suggestion("spend", "Model spending and limits", "", strong=True),
                    Suggestion("ask", "Ask Exo: " + q, q)]
        if not self.plain and q.startswith("/"):
            return [Suggestion("command", q, q, strong=True)]
        strong, weak = match_apps(q, [a["name"] for a in self.profile.data["apps"]])
        out = [Suggestion("app", f"Open {n}", n, strong=True) for n in strong[:MAX_SUGGESTIONS - 1]]
        out.append(Suggestion("ask", ("Answer: " if self.question else "Ask Exo: ") + q, q))
        out += [Suggestion("app", f"Open {n}", n) for n in weak]
        return out[:MAX_SUGGESTIONS]

    def submit(self) -> Action:
        """Enter in the palette: open the selected app, or send the text to Exo."""
        if not self.suggestions:
            return Action("none")
        pick = self.suggestions[min(self.selected, len(self.suggestions) - 1)]
        text = self.text.strip()
        self.set_input("")
        if pick.kind == "app":
            return Action("open_app", pick.value)
        if pick.kind == "spend":
            if not self.connected:
                self.lines.append(Line("error", NOT_RUNNING))
                return Action("none")
            self.lines.append(Line("you", "Show model spending and limits"))
            return Action("send", command=s.ModelsList())
        if pick.kind == "transcript":
            return Action("show_transcript")
        if pick.kind == "panels":
            return Action("show_panels")
        if pick.kind == "command":
            name, _, args = text[1:].partition(" ")
            return Action("send", text, s.CommandRun(name=name, args=args, session=self.session))
        if self.question is not None:
            q, self.question = self.question, None
            self.lines.append(Line("you", text))
            return Action("send", text, s.QuestionAnswer(question_id=q.question_id, answer=text))
        cmd = self._turn(text)
        return Action("send", text, cmd) if cmd is not None else Action("none")

    def _turn(self, text: str) -> s.TurnSend | None:
        if not self.connected or self.session is None:
            self.lines.append(Line("error", NOT_RUNNING))
            return None
        self.lines.append(Line("you", text))
        return s.TurnSend(session=self.session, text=text, mode="queue" if self.busy else "send")

    def app_failed(self, name: str) -> set[str]:
        self.lines.append(Line("error", f"I could not open {name}."))
        return {"transcript"}

    # -- errors and status

    def model_spend(self, value: Any) -> set[str]:
        from .spend import describe_spend
        self.lines.append(Line("note", describe_spend(value)))
        return {"transcript"}

    def error_text(self, code: str, message: str) -> str:
        if self.plain:
            return PLAIN_ERRORS.get(code, PLAIN_ERROR)
        return f"Error {code}: {message}" if message else f"Error {code}"

    def command_failed(self, code: str, message: str) -> set[str]:
        """A command exod refused (or one that could not be sent)."""
        self.lines.append(Line("error", self.error_text(code, message)))
        return {"transcript"}

    @property
    def status(self) -> str:
        """One calm line beside the prompt."""
        if not self.connected:
            return NOT_RUNNING
        if self.answering:
            return "Checking your answer…"
        if self.approvals or self.question:
            return WAITING
        if self.busy:
            return self.activity or WORKING
        return ""

    # -- what the view draws

    def stream_text(self) -> str:
        return "".join(self.streaming.values())

    def visible_lines(self) -> list[Line]:
        """While a turn runs the transcript is open: the recent lines and the text streaming in. After it
        ends the area collapses to the last exchange, so the answer stays readable until the bar hides."""
        recent = self.lines[self.shown_from:]
        if self.busy:
            return recent[-TRANSCRIPT_LINES:]
        last_you = max((i for i, ln in enumerate(recent) if ln.role == "you"), default=0)
        tail = [ln for ln in recent[last_you:] if ln.role != "tool"]
        return tail[-TRANSCRIPT_LINES:]

    def clear_transcript(self) -> set[str]:
        """The bar was hidden while idle: start the next opening clean."""
        if not self.busy:
            self.shown_from = len(self.lines)
        return {"transcript"}

    def recent_conversation(self) -> str:
        """A bounded local reading view; it never requests or implies complete session history."""
        recent: deque[str] = deque(maxlen=RECENT_LINES + 1)
        labels = {"you": "You", "exo": "Exo", "note": "Notice", "error": "Error", "tool": "Tool"}
        stream = self.stream_text()
        for line in chain(self.lines, [Line("exo", stream)] if stream else []):
            for index, chunk in enumerate(line.text.splitlines() or [""]):
                recent.append((labels.get(line.role, line.role) + ": " if index == 0 else "  ") + chunk)
        limited = len(recent) > RECENT_LINES
        shown = list(recent)[-RECENT_LINES:]
        while shown and len("\n".join(shown)) > RECENT_CHARS:
            limited = True
            if len(shown) == 1:
                shown[0] = "…" + shown[0][-(RECENT_CHARS - 1):]
                break
            shown.pop(0)
        note = "Earlier loaded content omitted by the 200-line or 100,000-character view limit.\n" if limited else ""
        return ("Recent conversation\n"
                f"Only currently loaded history is shown, up to {RECENT_LINES} recent lines and "
                f"{RECENT_CHARS:,} characters. Earlier conversation may be unavailable.\n"
                + note + ("\n".join(shown) if shown else "No conversation lines loaded yet."))

    def take_outbox(self) -> list[Any]:
        out, self.outbox = self.outbox, []
        return out


# -- palette matching


def match_apps(text: str, names: list[str]) -> tuple[list[str], list[str]]:
    """Split app names into strong matches (Enter opens them) and weak ones (shown, picked by arrow).

    Strong: the text is a prefix of the name or of a word in it ("whats", "app"), or "open <prefix>".
    Weak: the letters appear in order ("wapp"), only for one short word, so a question such as "hi" or
    "who do I call" is never swallowed by an app.
    """
    q = text.strip().lower()
    m = re.match(r"^(open|start|launch)\s+(.+)$", q)
    if m:
        q = m.group(2).strip()
    strong, weak = [], []
    for name in names:
        low = name.lower()
        words = re.split(r"[\s._-]+", low)
        if low.startswith(q) or any(w.startswith(q) for w in words if w):
            strong.append(name)
        elif " " not in q and len(q) >= 3 and _in_order(q, low):
            weak.append(name)
    strong.sort(key=lambda n: (not n.lower().startswith(q), len(n)))
    return strong, weak


def _in_order(q: str, s_: str) -> bool:
    it = iter(s_)
    return all(c in it for c in q)


# -- panel data, normalized for drawing


def panel_view(p: Panel) -> dict[str, Any]:
    """The panel's data in one fixed shape per kind. Accepts the looser shapes the panel kinds allow
    (plain string options, row objects; the nine kinds are listed in DESIGN.md §7) and never raises
    on bad data: the text fallback is always there."""
    d = p.data if isinstance(p.data, dict) else {}
    out: dict[str, Any] = {"kind": p.kind, "title": p.title, "text": p.text}
    try:
        if p.kind == "markdown":
            out["body"] = markdown_to_plain(str(d.get("markdown") or p.text))
        elif p.kind == "table":
            cols = [str(c) for c in d.get("columns") or []]
            rows = []
            for r in d.get("rows") or []:
                if isinstance(r, dict):
                    cols = cols or [str(k) for k in r]
                    rows.append([_cell(r.get(c)) for c in cols])
                elif isinstance(r, (list, tuple)):
                    rows.append([_cell(c) for c in r])
            out.update(columns=cols, rows=rows)
        elif p.kind == "tasks":
            tasks = []
            for t in d.get("tasks") or []:
                if isinstance(t, dict):
                    tasks.append((str(t.get("title", "")), str(t.get("status", "queued"))))
                else:
                    tasks.append((str(t), "queued"))
            out["tasks"] = tasks
        elif p.kind == "choice":
            opts = []
            for i, o in enumerate(d.get("options") or []):
                if isinstance(o, dict):
                    opts.append((str(o.get("id", i)), str(o.get("label") or o.get("id", i))))
                else:
                    opts.append((str(o), str(o)))
            out.update(prompt=str(d.get("prompt") or ""), options=opts, chosen=p.chosen)
        elif p.kind == "chart":
            labels = [str(x) for x in d.get("labels") or []]
            values = [_num(v) for v in d.get("values") or []]
            out["bars"] = list(zip(labels, values))
        elif p.kind == "diff":
            out["files"] = [(str(f.get("path", "")), str(f.get("diff", ""))) for f in d.get("files") or []
                            if isinstance(f, dict)]
        elif p.kind == "file":
            out.update(path=str(d.get("path", "")), content=str(d.get("content", "")))
        elif p.kind == "approval":
            out["summary"] = str(d.get("summary") or p.text)
    except (TypeError, AttributeError, ValueError):
        out = {"kind": "text", "title": p.title, "text": p.text}
    out.setdefault("options", [])
    return out


def markdown_to_plain(md: str) -> str:
    """Markdown read as wrapped text: headings, emphasis and code marks dropped, list marks as bullets."""
    out, code = [], False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            code = not code
            continue
        if code:
            out.append(line)
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)
        line = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"\1", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        out.append(line)
    return "\n".join(out).strip()


def _cell(v: Any) -> str:
    return "" if v is None else str(v)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _short_args(args: dict[str, Any]) -> str:
    text = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
    return text if len(text) <= 120 else text[:117] + "…"

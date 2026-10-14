#!/usr/bin/env python3
"""Synthetic Exo layer windows for a separate, same-user AT-SPI observer.

Only fixed local protocol events are constructed. Nothing connects to exod,
launches an app, reads a credential, or answers an approval.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading

from atspi_vm_guard import require_vm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-acceptance", action="store_true")
    args = parser.parse_args()
    try:
        require_vm(opt_in=args.vm_acceptance, fixture=True)
    except ValueError as exc:
        parser.error(str(exc))

    import gi  # imported only after the guard

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    if not Gtk.init_check():
        raise RuntimeError("Wayland GTK display unavailable")

    from exo.core.types import Message
    from exo.protocol import schema as s
    from exo_os.profile import Profile
    from exo_os.surface import ui as surface
    from exo_os.surface.model import SurfaceModel

    if not surface.layer_shell_ok():
        raise RuntimeError("Wayland compositor lacks gtk4-layer-shell")
    session = "s_atspi_fixture"
    seq = iter(range(1, 1000))

    def event(body):
        return s.Event.of(body, next(seq), session)

    model = SurfaceModel(Profile.default())
    model.set_session(session)
    sent = []
    ui = surface.SurfaceUI(model, sent.append,
                           launch=lambda *_: (_ for _ in ()).throw(AssertionError("app launch forbidden")))
    if not all(w.layered for w in (ui.bar, ui.drawer, ui.card)):
        raise RuntimeError("all three fixture windows must be Wayland layer surfaces")
    ui.on_connected(True)
    ui.show()
    ui.bar.entry.set_text("offline fixture question")
    ui.bar.entry.emit("activate")
    ui.on_event(event(s.TurnStarted(turn_id="t_atspi", model="fake:scripted")))
    ui.on_event(event(s.MessageDelta(message_id="m_atspi", text="AT_SPI_TRANSCRIPT_MARKER")))
    message = Message.assistant("AT_SPI_TRANSCRIPT_MARKER")
    message.id = "m_atspi"
    ui.on_event(event(s.MessageCompleted(message=message)))
    ui.on_event(event(s.TurnCompleted(turn_id="t_atspi")))
    loop = GLib.MainLoop()

    def reply(stage: str, *, error: str | None = None) -> None:
        response = {"stage": stage, "pid": os.getpid(), "layered": True,
                    "gsk_renderer": os.environ["GSK_RENDERER"],
                    "renderers": {name: renderer.__gtype__.name if (renderer := window.get_renderer()) else None
                                  for name, window in (("bar", ui.bar), ("drawer", ui.drawer), ("card", ui.card))},
                    "approval_responses": sum(isinstance(item, s.ApprovalRespond) for item in sent)}
        if error:
            response["error"] = error
        print(json.dumps(response, sort_keys=True), flush=True)

    def handle(command: str) -> bool:
        try:
            if command == "unsolicited":
                ui.on_event(event(s.UiPanel(panel_id="p_atspi", action="open", kind="choice",
                                            title="Synthetic choices", text="AT-SPI panel text",
                                            data={"prompt": "Choose one", "options": [
                                                {"id": "one", "label": "First choice"}]})))
            elif command == "approval":
                call = s.ToolCall(id="c_atspi", name="fixture_preview", arguments={
                    "body": "prefix " + "A" * 2048 + " AT_SPI_APPROVAL_SUFFIX"})
                ui.on_event(event(s.ApprovalRequested(
                    request_id="a_atspi", summary="Synthetic approval, read before consenting",
                    call=call, binding="fixture_only")))
            elif command == "status":
                pass
            elif command == "quit":
                reply(command)
                loop.quit()
                return False
            else:
                raise ValueError(f"unknown fixed fixture stage: {command}")
            reply(command)
        except Exception as exc:  # fixture failures must reach the observer, never look like a timeout
            reply(command, error=f"{type(exc).__name__}: {exc}"[:200])
            loop.quit()
        return False

    def read_commands() -> None:
        for line in sys.stdin:
            GLib.idle_add(handle, line.strip())
        GLib.idle_add(loop.quit)

    threading.Thread(target=read_commands, daemon=True).start()
    reply("ready")
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

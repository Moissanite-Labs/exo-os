# Surface keyboard and reading access

The prompt bar remains the starting point. Type an app name to open it or a question
to send it to Exo. Type `panels` and choose **Open panels** to move keyboard focus
into the panel drawer; Tab reaches its controls, and Escape closes the drawer and
returns focus to the prompt. Panels opened by Exo appear without taking focus.
Type `transcript` and choose **Read recent conversation** for a selectable,
read-only text view. Escape returns to the prompt. These local reading actions do
not ask the model or fetch history from the daemon. If Exo has asked a question,
Enter treats `panels` or `transcript` as the answer; use the second palette choice
to open the local view instead.

**Recent conversation** contains only history currently loaded in this surface,
up to the newest 200 text lines and 100,000 characters. Older loaded content is
omitted with a visible notice. History that was never loaded here is unavailable
in this view; it does not claim to show the complete session. The normal prompt
still collapses to the last exchange after a turn and clears when hidden.

An arriving approval preserves prompt text and keyboard focus. Its pending preview
has no keyboard grab; on backends without layer-shell, the pending summary appears
inside the prompt bar instead of mapping another window. **Review request (F6)**
opens the exact request for review. F6 is handled only while this surface owns
the keys, and initially focuses the card's selectable summary, before Yes or No.
A held F6 or a modifier change cannot reopen another request. Its release must
be observed before a fresh unmodified press enters a new keyboard review.
When the request includes a tool call, the card also shows its full literal JSON
arguments in a selectable, read-only text area in both plain and operator modes.
This includes long suffixes that a short summary may omit. Invisible direction
and format controls are escaped. The credential request card retains its separate
destination check and full outbound request preview. Y and N answer only during
explicit review, after the card's arming interval and a fresh unmodified key press; Ctrl, Alt, Super and
Meta combinations do not answer. Escape defers without sending a decision. A
response leaves review and returns focus to the prompt. A click on Yes or No can
answer the exact pending preview after its guard interval. Each button captures
the identity and full content it displays; an old button cannot answer a replacement.
The next queued request requires its own review gesture for keyboard consent.
Reopening the prompt preserves deferrals and ends review. A reconnect, pending
snapshot, uncertain answer, changed request or resolution also ends review;
neither reconciliation nor replay sends an answer automatically.

Pure model tests cover request text and reading limits. The private GTK display
smoke test checks focus, text selection, modifier and held-key behavior, and
direction changes when an Arabic or Urdu profile is applied. Private Broadway
checks widget focus and simulated text only. Phase 3 runs
the same synthetic checks on the disposable image's actual Wayland layer
surfaces, with real `wtype` delivery across request arrival, including initial Y/N
and the late `tod|ay` boundary, alongside its separate real protocol checks.
Its held-F6 evidence must show actual repeated delivery to the original handler
while the next request is pending. A duplicate discarded before delivery cannot
pass that coverage check. The temporary observer is removed afterward.
The timing-only unarmed assertions use a frozen clock so compositor latency cannot
turn a pre-arm assertion into an assertion after the guard has already expired.

The `vmtest` profile sets `GSK_RENDERER=cairo` only for `exo-surface.service`.
These VM checks therefore run on Wayland with Cairo. Production retains GTK's
default renderer selection. The harness verifies both the service declaration
and its running process environment before passing the same value to fixtures.
GTK evidence records each realized window's renderer through
[`Gtk.Native.get_renderer()`](https://docs.gtk.org/gtk4/method.Native.get_renderer.html)
and requires `GskCairoRenderer`; an environment hint alone cannot pass.

The service records are `p3-surface-renderer.json` and
`atspi-surface-renderer.json`; they contain only the renderer values and service
PID. Fixture renderer values appear in their existing JSON evidence. This scope
is software rendering in the disposable VM. Actual prompt, approval details and
buttons, drawer and RTL screenshots still need visual review on the new image.
Production GPU, hardware and low-power behavior remain unverified.

An additional, optional AT-SPI diagnostic can inspect a synthetic surface in
the disposable `vmtest` image. It is separate from Phase 3 and does not change
that suite's seven workload checks plus provenance (eight reported results).
After the intended `vmtest` image is available in this worktree's ignored
`image/out/` and `tools/dev-env.sh` has provided its local `.venv`, set
`IMAGE_SOURCE` to the full 40-character source commit recorded for that image.
Then run:

```sh
EXO_OS_VM_ACCEPTANCE=1 tools/diagnose-atspi.sh \
  --vm-acceptance --image-source "$IMAGE_SOURCE"
```
The diagnostic source must be a clean Git checkout, or a git-free archive with
its exact committed hash in `.EXO_OS_SOURCE_ID`; the latter is the standard
isolated build-host form. The harness requires the local `vmtest` build manifest
to name the supplied image source before QEMU; after boot, `accept_start`
compares the installed manifest and runtime/image source digest. It records
the diagnostic source separately.
The root harness binds SSH to its newly generated VM UUID and verifies the
installed pin and source digest before copying five diagnostic-only Python
files to root-owned `/run` state. It stops the actual surface temporarily,
launches the synthetic fixture as `exo` on that guest's Wayland compositor,
and restores the service afterward. It records the image source commit and
the separate diagnostic commit in `image/out/atspi-diagnostic.json` and a
result in `image/out/diagnose-atspi.txt`.

The fixture has no daemon connection, model request, credential, app launch or
private conversation. A separate same-user process selects only the fixture's
exact PID on the AT-SPI bus. It bounds discovery, tree depth, node count and
text length; it checks exposed roles, names, text, and focus for the prompt,
an unsolicited panel, explicitly opened panels and recent conversation, a
synthetic long approval, and Escape restoration. The panel and approval events
are synthetic; `wtype` supplies the explicit panel/transcript commands and
Escape keys on the real compositor. Failure JSON includes only names/roles/
focus from this fixed fixture, not other desktop processes' content.

This diagnostic has not yet run in the Linux VM. An AT-SPI tree pass would
establish only what this fixed fixture exposes. Orca navigation, speech output
and human reading order still need a separate human-assisted VM pass; no Orca
package or audio setup is installed by this diagnostic.

The inspector uses GNOME's documented [libatspi Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.Accessible.html),
[Text](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/iface.Text.html)
and [StateSet](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.StateSet.html)
GI interfaces. This tree inspection is a diagnostic of exposure, not a substitute
for a person's Orca reading session.

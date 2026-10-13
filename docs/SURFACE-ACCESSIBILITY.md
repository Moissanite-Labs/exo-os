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

An approval card initially focuses its selectable summary, before Yes or No.
When the request includes a tool call, the card also shows its full literal JSON
arguments in a selectable, read-only text area in both plain and operator modes.
This includes long suffixes that a short summary may omit. Invisible direction
and format controls are escaped. The credential request card retains its separate
destination check and full outbound request preview. Y and N answer only after
the card's arming interval and a fresh unmodified key press; Ctrl, Alt, Super and
Meta combinations do not answer. Escape defers without sending a decision. A
response closes the card and returns focus to the prompt.

Pure model tests cover request text and reading limits. The private GTK display
smoke test checks focus, text selection, modifier and held-key behavior, and
direction changes when an Arabic or Urdu profile is applied. Phase 3 runs
the same synthetic checks on the disposable image's actual Wayland layer
surfaces, alongside its separate real protocol and `wtype` checks.

An additional, optional AT-SPI diagnostic can inspect a synthetic surface in
the disposable `vmtest` image. It is separate from Phase 3 and does not change
that suite's seven checks. After the image built from
`e9cec25f63154fb15af72de43c6a9d14c74324bc` is available in this
worktree's ignored `image/out/` and `tools/dev-env.sh` has provided its local
`.venv`, run `EXO_OS_VM_ACCEPTANCE=1 tools/diagnose-atspi.sh --vm-acceptance`.
The diagnostic source must be a clean Git checkout, or a git-free archive with
its exact committed hash in `.EXO_OS_SOURCE_ID`; the latter is the standard
isolated build-host form. The harness requires the image manifest to name the
published `e9cec25` revision while separately recording the diagnostic hash.
The root harness binds SSH to its newly generated VM UUID and verifies the
installed pin and source digest before copying four diagnostic-only Python
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

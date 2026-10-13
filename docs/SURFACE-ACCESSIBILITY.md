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
direction changes when an Arabic or Urdu profile is applied. These checks still
need execution on the actual Exo OS image. AT-SPI exposure, Orca navigation,
Wayland layer focus, and reading order require a human-assisted VM pass; this
source change does not establish a screen-reader pass or install Orca.

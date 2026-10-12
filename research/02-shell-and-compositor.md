# 02. Desktop shell and compositor for Exo OS

Date: 2026-10-12. Status: research note, nothing built, nothing changed on any machine.

Reads first: `exo-audit/exo/research/UI.md` and `DECISIONS.md` D-014.
Marks: **[fact]** has a link. **[guess]** is my reasoning, not checked. No benchmarks here. Where a number
is missing, it is missing on purpose: measure it on the target machine.

## 1. What the shell must do

From D-014: one surface. It feels like a terminal (prompt plus transcript, `/` `@` `!`, one palette).
The model can open richer panels with a `ui` tool, which sends a `ui.panel` event. Panels: diff viewer,
task board, approval card, taste card, session tree, charts, file preview. Each client draws what it can.
The user can open or dismiss any panel by hand. Dismissals teach the learning loop.

So the desktop has two jobs:

1. Host the Exo surface so it is always one key away, and so panels can appear next to or over other apps.
2. Keep normal apps working. Chromium webapps (WhatsApp, Gmail, Drive, Meet, the CRM) matter to the first
   user, who is not technical.

Also: low RAM, accessibility, and a path that starts cheap.

Key point: the panel protocol is client-side. A panel is just a view in an Exo client. The compositor does
not need to know what a panel is. This lowers the stakes of the compositor choice a lot. **[guess, but it
follows from D-014's design]**

## 2. The options

### A. Keep Hyprland (Omarchy style) with a bar or widget toolkit

- Hyprland 0.55 (May 2026) moved config fully to Lua. The old syntax is supported for 1 to 2 more releases.
  [Phoronix](https://phoronix.com/news/hyprland-0.55-wayland-comp),
  [Linuxiac](https://linuxiac.com/hyprland-0-55-brings-lua-configs-and-user-defined-layouts/) **[fact]**
- Hyprland supports the layer-shell protocol, so bars, launchers and overlays work.
  [Flutter plugin docs](https://pub-web-s1.flutter-io.cn/documentation/wayland_layer_shell/1.0.0) **[fact]**
- Shell toolkits that sit on top:
  - Quickshell: QtQuick toolkit for desktop shells on Wayland and X11. Still alpha, breaking changes expected.
    Needs Qt 6.6 or newer. Build docs advise keeping jemalloc on, because the QML engine fragments memory
    otherwise. [Quickshell build notes](https://git.outfoxxed.me/quickshell/quickshell/src/commit/291179ede2c6b3af490f1cc81326fafc398e8ac8/BUILD.md),
    [package page](https://www.t2linux.com/packages/quickshell) **[fact]**
  - AGS (Astal, GTK widgets in JS/TS) and eww (own widget language). I found no source in this pass, so I
    only name them. **[unverified]**
- Pros: already on Shahid's machine and already tracked in the hydro-os repo. Tiling, workspaces, window
  rules and keybindings exist today. Zero new compositor code.
- Cons: Hyprland's config API is still moving (Lua just landed). Quickshell is alpha. One person's
  taste (Omarchy) sets the defaults, so some of it needs hiding for a non-technical user.
- Whether Omarchy's own shell (`omarchy-shell`, `shell.json`) is built on one of these toolkits: I did not
  check. **[unknown]**

### B. Custom layer-shell surface on a stock compositor

Write the Exo surface as a normal Wayland client that uses the layer-shell protocol (overlay, top, bottom
layers, anchors, keyboard focus modes).

- Python with GTK4: `gtk4-layer-shell` works from any language with GObject introspection, including Python,
  and ships a Python example. It also covers the session-lock protocol, so a lock screen is possible.
  [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) **[fact]**
- Supported on wlroots compositors (Sway, Hyprland), KDE Plasma, and Smithay compositors like COSMIC.
  Not on GNOME's Mutter, and not on X11.
  [gtk4-layer-shell README listing](https://github.com/wmww/gtk4-layer-shell),
  [Stack Exchange report on GNOME](https://backiee.wasmer.app/https_unix_stackexchange_com/q/800865) **[fact]**
- Other routes: Quickshell (QML), Rust with Smithay client toolkit or iced (**[guess]**: both exist, not
  checked), or a webview inside a layer-shell window.
- Pros: the Exo UI becomes a real shell part (drop-down terminal feel, side drawer for panels, overlay for
  approval cards) without owning a compositor. Core stays Python, and a Python client matches D-014's
  "Textual first, other clients later" plan.
- Cons: the surface is tied to compositors that offer layer-shell. Focus and global hotkeys depend on the
  compositor (Hyprland has bindings, but this is per compositor). **[guess]**
- GTK4 and accessibility: see section 5.

### C. Full custom compositor or shell

- Smithay: a Rust toolkit for compositors, modular, not a finished desktop. Used by COSMIC and niri.
  [Smithay](https://lib.rs/crates/smithay) **[fact]**
- COSMIC: Rust, iced toolkit. Epoch 1 shipped with Pop!_OS 24.04 LTS on 11 Dec 2025, after about three years.
  [LWN](https://lwn.net/Articles/1050268/),
  [DebugPoint](https://www.debugpoint.com/cosmic-1-1-0-desktop-environment-released/) **[fact]**
  It is a full desktop, not a library to bend into Exo. **[guess]**
- niri: Rust, on Smithay, scrollable tiling. It says plainly it is not a full desktop environment, so you
  add a bar and launcher. [niri](https://github.com/yaLTeR/niri) **[fact]**
- river 0.4 (April 2026): the compositor and the window manager are separate processes, speaking the stable
  `river-window-management-v1` protocol. You can swap the window manager without restarting apps.
  [Linuxiac](https://linuxiac.com/river-0-4-wayland-compositor-debuts-pluggable-window-managers/) **[fact]**
  **[guess]**: this is the most interesting "go deeper" path, because an Exo window manager could be a small
  program (even Python) and not a whole compositor.
- Wayfire: Compiz-inspired 3D compositor on wlroots, plugin based.
  [Wayfire](https://github.com/wayfireWM/wayfire) **[fact]** Not a fit for "terminal feel". **[guess]**
- Pros: full control (the model could place windows, glass panels, one focus model).
- Cons: this is a multi-year product by itself. COSMIC took three years with a funded team.
  Input, outputs, screen sharing, portals, XWayland, HiDPI and accessibility all become your problem.
  A solo or small team should not start here. **[guess, but the COSMIC timeline supports it]**

### D. Kiosk model (cage)

- cage is a wlroots kiosk compositor. It runs one maximized app. It blocks moving, resizing and minimizing.
  Run from a TTY it uses KMS and DRM. [cage](https://github.com/emersion/cage),
  [Phoronix](https://phoronix.com/news/Cage-0.2-Released) **[fact]**
- Pros: tiny, almost no policy, very stable. Good for a device (the companion horizon in UI.md) or a
  demo box.
- Cons: one app only. Chromium webapps and the CRM would have to live inside the Exo surface or be
  reached some other way. No window switching. That breaks the first user's daily work.
- Fit: a good target for a future Exo device image. Not for Shahid's desktop. **[guess]**

### E. Web-tech shell (Electron, Tauri, webview, on cage or on any compositor)

- Electron runs on Wayland with Chromium's Ozone flags (`--ozone-platform=wayland` or the hint option).
  Source is forum advice only, so check the Electron docs before relying on it.
  [thread](https://lemmy.klein.ruhr/post/2144/11420) **[weak source]**
- Tauri on Linux uses WebKitGTK. Developers report rendering and Wayland differences across distros and
  GPUs. [post](https://paseo.sh/blog/i-was-wrong-about-electron) **[anecdote]**
- Memory: one GitHub issue measured Electron at about 118 MB USS (207 MB PSS) and Tauri at about 125 MB
  USS (185 MB PSS) on Ubuntu, close together. Other articles claim a big gap with no method.
  [Tauri issue 5889](https://github.com/tauri-apps/tauri/issues/5889) **[one measurement, not mine]**
  Do not pick on RAM claims alone. Measure.
- Pros: the richest panels (charts, diff views, markdown, file preview) are cheapest to build in HTML.
  The web client for Exo (D-014 says web and desktop clients render full panels) is needed anyway.
- Cons: each webview is a second browser engine. Adding a webview shell next to Chromium webapps costs
  RAM and attack surface. **[guess]**
- Layer-shell from a webview: I found no source. Likely needs a native wrapper. **[unknown]**

## 3. Comparison

| Option | Effort | RAM (guess, measure) | Chromium apps | A11y path | Fit now |
|---|---|---|---|---|---|
| A. Hyprland plus bar | low | low | work as today | same as today | yes |
| B. Layer-shell surface | low to medium | low (Python GTK4) | work | via GTK4 | yes, next |
| C. Custom compositor | very high | lowest possible | need a lot of glue | all on us | no |
| D. cage kiosk | low | very low | none | limited | device only |
| E. Web-tech shell | medium | medium to high | work | Chromium a11y | for panels, inside B |

Reading it: A plus B, with E used only as the panel renderer, covers the goal with the least risk.

## 4. Chromium webapps

- Chromium `--app=URL` windows are ordinary Wayland clients (xdg-shell). They work on every compositor
  above except cage-as-sole-app. **[guess, standard behavior, not re-checked]**
- On Omarchy these are installed with `omarchy webapp install` and matched by window class (from the
  hydro-os brief). Keep that. The Exo layer must not replace it.
- Exo should treat them as apps it can launch and focus (`/open whatsapp`), not as things to embed.
  Embedding a second Chromium inside Exo would double RAM for no gain. **[guess]**
- The CRM (local web page, port 8741) is the same: a webapp window. Exo never touches that process.

## 5. Accessibility

- AT-SPI2 is the D-Bus protocol toolkits use to expose widgets to screen readers like Orca.
  [freedesktop wiki](https://wiki.freedesktop.org/www/Accessibility/AT-SPI2/) **[fact]**
- Wayland gaps: a Red Hat issue says Orca gets no keyboard input from GTK4 apps on Wayland, since GTK4 no
  longer uses the old keyboard notification API. A 2023 GNOME meeting noted Wayland has no authorization
  step for accessibility clients. [RHEL-65520](https://issues.redhat.com/browse/RHEL-65520),
  [meeting notes](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/meeting-2023-01-13.html)
  **[fact, may be dated]**
- niri 25.08 added basic screen reader support: the `org.freedesktop.a11y.KeyboardMonitor` D-Bus interface
  and AccessKit for its own UI. [LWN](https://lwn.net/Articles/1036625) **[fact]**
  This shows a small compositor can do it, but it is work.
- Takeaways **[guess]**:
  - A terminal-like surface is good for a11y in principle: text in a linear order. Keep the transcript as
    real text, not painted pixels.
  - Every panel needs a text form. The `ui.panel` event should carry a plain-text summary, and clients must
    expose it. This matches D-014's "terminal renders a compact form". It also serves voice and screen
    readers.
  - Large text, high contrast and keyboard-only use are cheap wins. Omarchy already has a text size command.
  - A custom compositor (option C) makes all of this our job. Another reason to avoid it now.
  - Test with Orca early on whichever client we pick. I did not test any of this.

## 6. Non-technical user (Shahid)

- Fewer things on screen, fewer keys. His current layout is SUPER+1..5 for apps and a printed key card.
  Exo should add one key: summon Exo. Then he talks or types, and Exo opens what he needs.
- He does not need to know about panels. A panel is "the thing that showed up". It must be easy to
  dismiss (Esc) and must never hide the app he was in without a way back.
- Approval cards are the key moment. They must be big, plain, and say what will happen. **[guess]**
- Do not make him learn tiling. Keep apps maximized as the hydro-os rules do now.
- Recovery: a visible way back to a normal desktop if Exo fails. A kiosk or custom shell with no exit is a
  risk (the cage listing itself warns kiosk misconfig can trap users). **[fact, from the cage search]**

## 7. Low RAM

- No numbers from me. Measure PSS of each piece on the real machine before choosing.
- Where RAM goes **[guess]**: every browser-engine process (Chromium webapps, any Electron or WebKit shell),
  then Qt/QML shells, then GTK apps, then native Rust or C clients.
- Rules of thumb that follow:
  - Run one Exo core (Python, shared by all clients).
  - Run at most one rich renderer (one webview, or none). Terminal and GTK clients stay light.
  - Start panels on demand and close them when dismissed. The model summons, so idle cost is near zero.
  - Quickshell: keep jemalloc on (build docs). Watch for slow growth. **[fact from build notes]**
  - Compare any new shell against Omarchy's current one by PSS (`smem` or `/proc/*/smaps_rollup`).

## 8. A day with terminal feel and summoned panels

This is a picture, not a spec. Shahid, a non-technical user, on his Omarchy machine.

**Morning.** The screen shows his last app, maximized. He presses SUPER+Space and Exo drops in as a bar
at the top (a layer-shell surface, one line, a cursor). It looks like a terminal prompt. He types
"what is new today". Text streams in. Exo finds three emails and two CRM items.
It does not open Gmail. It writes four short lines.

**Panel 1, task board.** One line says "5 things, show board?". It is a hint, not a pop-up, because the
decision model (D-015) judged he usually ignores boards in the morning. He presses Enter on a key shown
next to the hint. The board slides in as a side drawer. Three items are done, two wait for him. He presses
Esc. The drawer closes. That dismissal is logged as a signal.

**Midday, files.** He types "@" and picks a PDF quote. Exo opens a file preview panel beside the transcript.
He asks "what changed since version 2". Exo opens a diff panel with two columns. He reads, says "send
it to Ahmed". An approval card appears, centered, large, plain words: "Send this file to Ahmed by email.
Yes or No." He presses Y. The send happens. The card shows "Sent" and fades.

**Afternoon, apps.** SUPER+1 opens WhatsApp, SUPER+2 Gmail. Exo is not in the way. He asks Exo
"open the CRM". Exo launches the CRM window on workspace 9 through the same window rules as today. It does
not embed or touch it.

**Charts.** Late day he types "how were sales this month". A chart panel opens. If he were on a phone or
the companion device, the same event would give a card with a sentence, or a spoken answer. Same
`ui.panel` event, different client. This is D-014.

**Evening.** He walks away. After 15 minutes the screensaver starts, then the lock after an hour (hydro-os
settings). Exo holds no panel open. Idle RAM is the core plus a small client. **[guess]**

What the walk-through demands from the stack:

- An always-available input surface (layer-shell, overlay layer, keyboard focus on demand).
- A drawer and a centered card as two window shapes. Both can be layer-shell or normal floating windows.
- Hint lines inside the transcript, so panels are offered before they force themselves.
- A text form of every panel (a11y, terminal, voice).
- Dismiss by Esc everywhere.
- Launch and focus of ordinary apps (already built in the hydro-os layer).

None of this needs a new compositor.

## 9. Recommendation

**Keep Hyprland. Build the Exo surface as a client, then add richer clients. Revisit the compositor only
when a real need appears.**

Why:

1. The compositor choice does not decide the product. D-014 puts panel logic in the protocol and the
   client. So the cheapest compositor that already works is the right start.
2. Hyprland is already running, tracked in hydro-os, and carries Shahid's working layout. Switching has
   cost and no user gain.
3. Writing a compositor (option C) is years of work with accessibility, portals and HiDPI all falling on us.
   COSMIC's three years with a team is the reference. **[fact + guess]**
4. cage (D) kills the Chromium webapps the first user depends on. Keep it only as a device-image idea.
5. A web-tech shell (E) is worth it for panels only. Use one webview, on demand, not as the desktop.
6. Hedge: Hyprland's config is in flux (Lua just landed), and Quickshell is alpha. Keep our own layer thin
   so we could move to niri, river or sway later. The Exo client speaks Wayland layer-shell and the Exo
   protocol, nothing Hyprland specific except a small keybinding and window-rule file. **[guess]**

Risks to watch: layer-shell keyboard focus quirks, Orca on GTK4 under Wayland, Hyprland Lua churn, RAM
growth in a long-running Quickshell or webview.

## 10. Phased path

**Phase 0, now (days, no new code).**
- Keep the hydro-os layer. Add one binding that opens the Exo TUI client in a terminal window (a drop-down
  via a Hyprland special workspace, **[guess]** this exists as a feature, not checked this pass).
- Use the Textual client from UI.md. Panels render in compact text form. This proves the terminal feel.
- Measure PSS of the current shell and of Exo's client. Write the numbers down.

**Phase 1 (weeks): layer-shell surface.**
- A small client with `gtk4-layer-shell` (Python GTK4) or Quickshell: top-edge prompt bar, side drawer,
  centered card. First panels: approval card, diff, task board, as native widgets or simple markdown.
- Add the text form of each panel and test with Orca. Large-text mode for Shahid.
- Decision gate: GTK4 or Quickshell. Pick by RAM and by Orca behavior, measured.

**Phase 2 (months): rich panels.**
- Add one on-demand webview client for charts and file preview (Tauri or a light webview), reusing the web
  client of Exo. Close it when idle. Measure PSS before and after.
- Learning loop: log panel open and dismiss events to the ledger (D-014).

**Phase 3 (later): go deeper only if needed.**
- If window placement by the model matters, try a river window manager (river 0.4 separates it from the
  compositor) or niri. This is a small program, not a compositor. **[guess]**
- Device image: cage plus the Exo client, for the companion device. Separate track.
- Custom compositor: only with a team and a clear reason. Not before phase 3 results.

Exit rule at each phase: if Shahid's day gets harder or RAM rises past a budget agreed with A, stop and
go back one phase.

## 11. Open questions

- Does Omarchy 4.0.4's own shell use Quickshell or something else? If it does, Exo can share it. Check
  `omarchy-shell` on the machine (read only).
- Does any Orca plus GTK4 layer-shell combination work today on Hyprland? Needs a test.
- What RAM budget does A want for the whole desktop? A number is needed before anyone can say "too heavy".
- How should the decision model (D-015) learn "offer a hint" versus "open now"? A product question for the
  learning loop, not a shell question.
- AGS and eww: not researched here. Do one pass if option A grows.

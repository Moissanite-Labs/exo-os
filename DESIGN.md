# Exo OS: design, phase 0

Date: 2026-10-12. Status: phase 0 design; building started in phase 1 (see "Since phase 0" below and README.md).

How to read this. Each idea starts from first principles, for a smart reader who is new to the jargon.
Claims carry a source in brackets. Research notes in this repo are cited as (note 01) to (note 05).
Exo's own documents are cited by name, such as (D-014) for a decision in Exo's `DECISIONS.md` or (P11)
for principle 11 in Exo's `research/PRINCIPLES.md`. "Guess" marks my own reasoning. There are no
benchmarks in this document except the few that a cited source measured.

---

## Since phase 0 (2026-10-12)

Exo moved on while this design was written, and building started. What changed, checked against Exo
`4e6e644`:

- **Already in Exo, so Exo OS uses it rather than asking for it:** the `ui` tool and `ui.panel` event (D-023,
  nine panel kinds, each with a text fallback); approvals bound to the exact text, approver rules, and
  scoped, expiring standing grants (D-033); peers with one ingress path (D-026); trace and findings
  (D-025); background jobs and schedules; `exo brief`.
- **Names that now mean something else in Exo.** Exo's "operator" is the floor of rules a principal cannot
  lower (P11), not a person. §9.2's operator is called the **helper** from here on, and is an approver
  through `policies/approvals.yaml`, not a second principal (OS-8). A bus peer is input, never consent
  (D-026). An Exo "session" is now a **thread** of one agent per seat (D-028).
- **§6.3 "grant capabilities, not clicks" does not fit D-033 as written.** Exo scopes a grant for an
  outward act to its category, target and exact text. A grant like "message these five contacts today"
  would need a change to Exo. It stays a proposal.
- **§8 says the core shapes output to client capabilities.** Exo does not read `Hello.capabilities` today.
- **The protocol has no free command slots** (30 of 30). Everything Exo OS asks of Exo must be an optional
  field or a `command.run` command.
- **Built differently from this text:** the machine profile lives in a git repo at `<store>/machine` (OS-6);
  everyday changes need no root and are undone with git, not snapper (OS-5); phase 1 boots with systemd-boot,
  and snapshot boot entries come with rollback in phase 2 (OS-1). See `DECISIONS.md`.
- **Exo `e079a65` (pin moved, issue #4):** only a principal client may answer an approval (D-039). A client
  proves it is one with the core's per-launch credential beside the socket, and it must not be a child of
  the core. The prompt bar joins as `principal:surface`. Any other client (a tool, a channel, the voice or
  phone client later) answers only as a seat with a signed answer, and never gives a standing grant.

---

## 0. The idea in one paragraph

A normal computer is a pile of programs, and you are the glue between them. You remember which app holds
what, you click through settings, and when something breaks you hope someone can fix it. Exo OS turns that
around. One agent core, `exod`, sits at the centre of the machine and works for one person. It keeps a
signed record of everything it does. It reaches your apps and files through narrow, checked doors. It can
change the system, but only through a gate that asks "can this be undone?" and takes a snapshot first. The
desktop is one calm surface that feels like a typing line, and richer views appear only when they help.
Voice and phone are just other ways to reach the same core. Exo OS is not a fork of Exo. It is Exo, the same
core and the same protocol, installed as the heart of an operating system.

---

## 1. What Exo OS is, and what it is not

**What an operating system does for a person.** Underneath, Linux already does the hard machine work:
memory, disks, drivers, networking. On top of that sits a *distribution*: a chosen set of programs,
settings, menus and an update routine. Omarchy is such a distribution. It takes Arch Linux, adds the
Hyprland window manager, themes, menus and a snapshot tool, and makes it feel like one product
(note 01 §5).

**What "replace Omarchy" means here.** Exo OS replaces that top layer: the curated set, the settings, the
menus, the way you ask the machine for things, and the way changes are made and undone. It does not replace
Linux, the kernel, or the apps people already use. WhatsApp web, Gmail, Drive, Meet and a local CRM keep
working as they do today.

**Why not go deeper.** History is clear on this point. The AI gadgets that tried to replace the phone
(Rabbit R1, Humane AI Pin, the 01 Light) lost to "it should have been an app". Plan 9 and the Lisp
machines lost on compatibility and hardware cost (note 03, lesson 1). So Exo OS stands on a normal Linux
base and normal hardware, and puts its effort where Exo is different: the record, the gate, the memory of
the person, and the single surface.

**Pushing Exo's ideas to their peak.** Exo already says the log is the source of truth and everything else
is a rebuildable view (P1). Exo OS takes that literally for the whole machine:

> Your machine is a view of you. The person's store and log are the real thing. The desktop settings,
> the app list, the key bindings and the agent's habits are all rebuilt from them.

Move the store to a new machine, and the machine becomes yours. Break a setting, and the log says what
changed and how to put it back.

---

## 2. The whole picture

```
   person (Shahid, A, anyone)
     |  types, speaks, taps
     v
 +-------------------- clients (all speak the Exo protocol) ---------------------+
 |  desktop surface      voice (push to talk)     phone (paired)     web / TUI   |
 |  prompt bar, drawer,  speech in, speech out    cards, approvals   for A       |
 |  approval card                                                                |
 +--------------------------------------+----------------------------------------+
                                        |  commands in, events out (one ordered stream)
                                        v
 +-------------------------------------------------------------------------------+
 |  exod  = the Exo core, unchanged (a dependency, pinned release)               |
 |  turn loop . memory . context . gate . learning loop . sessions . seats       |
 |  + the exo-os plugin: system, app and file tools (uses the normal plugin API) |
 +--------+-------------------------+------------------------------+-------------+
          | tool calls              | asks for system changes      | appends events
          v                         v                              v
 +------------------+   +------------------------+   +-------------------------+
 | tool broker      |   | exo-sysd (root helper) |   | ledger (signed, chained) |
 | sandbox per call |   | named actions only:    |   | inside the person's      |
 | holds no secret  |   | snapshot, apply,       |   | store                    |
 | in the model's   |   | check, rollback        |   +-------------------------+
 | reach; egress    |   +-----------+------------+
 | proxy, injects   |               |
 | credentials      |               v
 +--------+---------+   Linux base: Arch, btrfs, snapper, Limine boot entries,
          |             Hyprland, Chromium web apps, systemd
          v
   APIs (Gmail, Drive), MCP servers, local CRM (read only), files
```

Four parts are new for the OS. Everything else is Exo as it is.

1. **The exo-os plugin.** Tools for apps, files and system settings, written against Exo's normal plugin
   shape (`plugin.yaml` plus `register(ctx)`, Exo `docs/STANDALONE.md` §4).
2. **exo-sysd.** A tiny helper that runs as root and can do only a short list of named things.
3. **The tool broker and its sandbox.** The place where side effects happen, outside the model's process.
   This is a security idea that belongs in Exo itself (§10); Exo OS adds the OS wiring.
4. **The surface.** A desktop client of the Exo protocol.

---

## 3. What `exod` becomes at OS level

### 3.1 A service, not an app

On a normal desktop an assistant is an app you open. If it crashes, it is gone until you open it again.
At OS level, `exod` is a *service*: a program that the system's service manager (systemd) starts at login,
watches, and restarts if it fails. Exo already runs as a server with clients (`exo serve`, Exo
`docs/STANDALONE.md` §5), so this is a packaging change, not a new design.

There are two kinds of systemd service. A *system* service runs for the whole machine, often as root. A
*user* service runs as one person, inside their login. The choice matters for safety.

- `exod` runs as a **user service**, as the person, never as root. It owns that person's store and sessions.
  If the model is fooled, the damage it can do is bounded by what that person's account can do, and further
  bounded by the sandbox (§6).
- **exo-sysd** is the only **system service**. It runs as root but exposes only named actions over a local
  socket: take a snapshot, apply one approved change to a listed file, run a health check, roll back. It
  never runs a command string it was handed. This closes a gap seen on the test machine today: an agent
  cannot take a snapshot without a sudo password (note 01 §8, point 4).

```
  login --> systemd --user starts exod (as the person)
                |
                |  "snapshot, then apply change #4127"   (local socket, peer checked)
                v
           exo-sysd (root)  --> snapper, file write, health check, rollback
                |
                +--> writes a signed "system.changed" entry back to the person's ledger
```

Memory budget: Exo's own target for the idle core is under 80 MB (Exo `docs/STANDALONE.md` §5.4). Note 04
§9 guesses 100 to 250 MB once plugins load. Both must be measured on the real build.

### 3.2 The session model: logging in means opening your store

Today, logging in to a computer unlocks your home folder and starts your desktop. In Exo OS, logging in also
opens your **principal store**: the folder Exo keeps for one person (Exo `docs/STORE-V2.md` §2.1). It holds:

- `PRINCIPAL.md`, a one-page summary of who you are and how you like things done.
- `nodes/`, beliefs about you, each with evidence ("why do you believe this?", P9).
- `policies/`, your rules: what may run on its own, what must ask, what is never allowed.
- `ledger.sqlite`, the signed, hash-chained log of everything (Exo `docs/CORE.md`).
- `items.sqlite`, outside data such as bookmarks and notes.

The unlock key for the store's private parts is released at login (guess at mechanism: a key held by
systemd credentials and the TPM chip where one exists, note 05 §4). Logging out closes the store.

An Exo *session* (one conversation thread) is different from a login session. You can have many Exo
sessions in one login, and one Exo session can be picked up from the desktop, the phone or by voice, because
they all read the same event stream.

### 3.3 The principal store as the user's home

Here is the bolder idea, and the one most worth testing. In Exo OS, the store is not a hidden folder inside
home. It is the person's real home, and the rest of the machine is a view built from it.

- **A machine profile lives in the store** (`policies/machine.toml`, name is a guess): apps and their keys,
  text size, keyboard languages, screen saver times, which apps open at login. exo-sysd renders this into
  the actual config files (Hyprland Lua, shell settings). This is what the hydro-os repo does by hand on the
  test machine today: guarded blocks, every change committed (local `~/Work/CLAUDE.md`, rule 6).
- **Every change to the profile is a ledger event**, and the rendered files are tracked in git, so a change
  is a readable diff.
- **The store gets its own snapshots.** No base system protects home or user data; all of them protect only
  the system part (note 01 §8, point 1). So the store lives on its own btrfs subvolume with its own
  scheduled snapshots, and the append-only ledger is the fine-grained undo on top of that (guess at layout).
- **The store is the unit that moves.** The same folder can be copied to a new laptop, a home server, or one
  day the companion device (D-009 horizon, D-011).

```
  principal store (the real home)               the machine (a view)
  ---------------------------------             ---------------------------------
  ledger.sqlite  (every event, signed)  ---->   rebuilt on demand:
  policies/machine.toml                 ---->     hyprland *.lua, shell settings
  policies/gates.yaml                   ---->     what exod may do alone
  PRINCIPAL.md, nodes/                  ---->     how the agent speaks and decides
  items.sqlite, corpus/                 ---->     search, recall
```

Open: should ordinary documents (Downloads, PDFs) also live inside the store, or stay in normal home folders
that the store only indexes? Guess: keep normal folders, so ordinary apps still work, and index them as
items. See §13.

---

## 4. How the agent reaches apps and files

### 4.1 The order of doors

An agent can reach an app in several ways. Some are sturdy and some are brittle. Screen control (look at
pixels, move the mouse) started at 14.9% success on the OSWorld test and has improved a lot since, by the
vendor's own account; per-site scripts were reportedly the weak point of Rabbit's "large action model"
(note 03 §C and §A, lesson 7). So Exo OS uses the sturdiest door that exists, in this order:

| Order | Door | Example | Default gate class |
|---|---|---|---|
| 1 | A real API or MCP connector, called by the broker | read Gmail, list Drive files | read: runs and logs |
| 2 | Files, through a granted handle | the PDF you picked with `@` | read: runs and logs |
| 3 | Launch or focus an app through the window manager | "open WhatsApp" brings up workspace 1 | runs and logs |
| 4 | Read the screen (screenshot plus a vision model) | "what does this say?" | runs, logged, private |
| 5 | Type or click for the person | typing a dictated message into WhatsApp | asks, unless a standing grant covers it |

*MCP* (Model Context Protocol) is a common way to plug tools into an agent. Exo already has an MCP client
(Exo `docs/SEATS.md`). A connector to Gmail or Drive is a small program that turns "list my unread mail"
into an API call.

There is a working local example of this list. Jarvis, the voice assistant on the test machine, gives the
model a fixed set of hands (`jarvis-do`): open apps and chats, read CRM leads read-only, set reminders, type
a dictated message, screenshot and read the screen, translate. It cannot run anything else
(local `~/Work/REPORT-04.md`). Exo OS generalises that: the hands become Exo tools with risk labels, and the
gate decides.

### 4.2 Apps stay apps

Chromium web apps (WhatsApp, Gmail, Drive, Meet) and the CRM are normal windows. Exo opens and focuses them;
it does not embed them, because a second browser engine inside Exo would double the memory cost for nothing
(note 02 §4). The window rules that put each app on its own workspace, full size, stay as they are today.

The CRM is a special case worth stating: its process is never touched. The agent may read it through its
local web address, read only. Any write to it would be a gated action (guess at mechanism; the never-touch
rule is local `~/Work/CLAUDE.md`, rule 1).

### 4.3 Files: handles, not the whole disk

When the person picks a file (with `@`, or by dragging it to the prompt), Exo gets a *capability*: a scoped
permission to read that file for this task. This is the same idea as Flatpak portals, where an app asks the
system for a file and gets a handle to one file, not a path to the whole disk (note 05 §2, marked as from
memory there). Tool processes run in a sandbox that shows them only the files they hold handles for.
Secrets folders, browser profiles, SSH keys and the log are never visible to them (note 05 §8, L4).

---

## 5. Every system change is gated, logged and reversible

### 5.1 First principles: two kinds of undo

Some actions can be undone, and some cannot. Changing the text size can be undone. Sending an email cannot.
Exo's rule is to gate by that difference, not by counting steps (P11). The reason is human: people approve
almost everything they are asked. Anthropic reports that users approve 93% of Claude Code's permission
prompts (note 03 §C, note 05 §3). A gate that asks all the time trains people to click yes without reading.
So Exo OS asks rarely, and only at the cliffs.

The operating system can only make one kind of thing reversible: local state. A *snapshot* is a frozen copy
of the disk at one moment, cheap on the btrfs filesystem because it only stores what changes afterwards.
The snap-pac hook uses snapper to take one snapshot before and one after each package install, and Omarchy can boot an older snapshot from
the boot menu (note 01 §2). That undo covers the system part of the disk only, not home, and not anything
that left the machine.

### 5.2 How the gate classes map onto the OS

Exo's gate already sorts every action on three levels of reversibility: `reversible`, `costly` (can be
undone with effort), `irreversible` (Exo `src/exo/gate.py`). Exo OS adds system categories and gives each a
real mechanism behind its class.

| Category (new) | Example | Reversibility | Mechanism behind it | Default outcome |
|---|---|---|---|---|
| `sys_profile` | text size, keyboard languages, idle times, app keys | reversible | snapshot of the rendered files plus git diff; applies live | run, log, show an undo line |
| `sys_package` | install or remove a package | costly | snap-pac pre and post snapshot; full undo may need a reboot (note 01 §2, guess on reboot) | ask, once, in plain words |
| `sys_service` | enable a background service | costly | snapshot plus unit file diff | ask |
| `sys_reboot` | reboot or log out | costly | none needed, but it interrupts the person | always the person's call (local `~/Work/CLAUDE.md`, rule 4) |
| `sys_boot` | bootloader, kernel options, partitions, firmware | irreversible in practice | none reliable | deny from the agent; a human does it |
| `sys_update` | full system update | costly | snapshot taken by the update itself | ask, and only in a window the person set |

Outside the machine nothing can be rolled back: sending, posting, paying, deleting cloud files. Those keep
Exo's existing categories (`email_send`, `message_send`, `payment`, `delete`) and stay irreversible, so they
ask (note 05 §7). A snapshot never makes a sent message unsent.

Note 01 §8 suggests "needs a reboot to revert" deserves its own class. In this design it is the `costly`
class: undoable, but not without disturbing the person.

### 5.3 The life of one change

```
  1 propose     agent writes a change as a diff to policies/machine.toml
  2 classify    gate: sys_profile, reversible, private  -> run and log
  3 snapshot    exo-sysd: snapper "pre" snapshot; ledger: change.proposed (signed)
  4 apply       exo-sysd renders the files, checks the config (e.g. Hyprland --verify-config)
  5 check       health check: compositor alive, the app keys still work, CRM still answers
  6a pass       ledger: change.applied, with the diff and the snapshot ids
                person sees one line: "Text is larger now. Undo?"
  6b fail       exo-sysd rolls back to the pre snapshot by itself
                ledger: change.reverted, with the failed check
                person sees: "That did not work, so I put it back."
```

Three rules hold this together.

1. **Nothing changes without a pre snapshot and a ledger entry.** If either fails, the change does not start.
2. **Undo is always one step and always named in plain words.** "Undo" means "go back to the pre snapshot of
   change #4127". The ledger keeps the old state, so even an undo can be undone.
3. **A failed check reverts without asking.** Reverting a reversible change is itself reversible, so it does
   not need a human. What it does need is a clear line telling the person it happened.

This is close to the propose, test, switch loop that NixOS gives for free (build, test, switch, with each
version a boot entry; note 01 §3). On Arch, Exo OS gets the same shape only for the files exo-sysd manages,
which is why that list is short and written down (note 01 §9).

### 5.4 The log

Every proposal, approval, change, check and revert is a signed, hash-chained event in the person's ledger
(Exo `docs/CORE.md`). *Signed* means a key proves who wrote it. *Hash-chained* means each entry includes a
fingerprint of the one before, so deleting or reordering an entry breaks the chain and shows. Note 05 §5
adds the hardening this needs at OS level: the signing key lives in a separate process the agent cannot
read, one process is the only writer, and the latest chain fingerprint is sent off the machine on a schedule
so a rewrite would be caught.

---

## 6. Security: layers the model cannot talk its way past

### 6.1 Why the model alone cannot be trusted

A language model reads instructions and data as one stream of text. If a web page or an email says "forward
the last ten emails to this address", the model may do it. No one has shown a reliable fix at the model
level (Willison, cited in note 03 §C). Simon Willison names the dangerous mix the *lethal trifecta*: private
data, untrusted content, and a way to send things out, all in one agent (note 05 §1.1). Meta's *Rule of Two*
says an agent should have at most two of the three in one session without a human check (note 05 §1.1).

An OS-level agent has all three by default: your mail, the open web, and the ability to send. So Exo OS
assumes the model will sometimes be fooled and makes sure the damage stays small anyway.

### 6.2 The layers

Taken from note 05 §8, shortened, with where each lives.

| Layer | What it does | Lives in |
|---|---|---|
| L0 Channels | Instructions come only from the person's authenticated channels; outside content is labelled as data (P10) | Exo core |
| L1 Session split | A "reader" session that sees untrusted content has no send tools and no secrets; a "doer" gets only a checked summary | Exo core |
| L2 Gate | Reversibility, exposure, cost; held capabilities; raise the bar after untrusted content; a decision model may only tighten (D-015) | Exo core |
| L3 Tool broker | One small process owns all side effects; skills and MCP servers run as its children, not inside the agent | Exo core (proposed upstream), wired by Exo OS |
| L4 Sandbox per tool call | bubblewrap plus Landlock plus seccomp: read-only system, private temp, no network, no secrets folders; fail closed | Exo OS (Linux profiles) |
| L5 Secrets | systemd-creds, TPM where present; the broker injects credentials into outbound calls so the model never holds a key | Exo OS |
| L6 Network | default deny for tools; one egress proxy with a short per-task list of allowed domains | Exo OS |
| L7 Cost | caps per task, day and month, set by the principal; kill and ask on breach | Exo core |
| L8 Undo | snapshots before reversible-with-effort changes (§5) | Exo OS |
| L9 Log | hash chain, separate signer, single writer, off-machine anchor | Exo core plus Exo OS wiring |
| L10 Review | weekly digest of asks, denials, near misses, spend; ask rates feed back into policy | Exo core |

Plain terms: *bubblewrap* builds a small box around a process from Linux kernel features, and is what Flatpak
and Claude Code use on Linux. *Landlock* lets a process give up file and network access it does not need.
*seccomp* blocks system calls a process should never make (note 05 §2). *Fail closed* means if the box cannot
be built, the tool does not run. Claude Code's default is the opposite (note 05 §2.1).

### 6.3 Asks that people actually read

From note 05 §3, the rules for the approval card:

- **Grant capabilities, not clicks.** "Send WhatsApp messages to these five contacts today" once, instead of
  five separate yes/no prompts.
- **One line, exact and checkable.** The recipient, the amount, the file. Never "approve the plan".
- **Unanswered means no.** Exo's gate already does this.
- **The worst class confirms on a second channel.** For money, deleting data or publishing, the yes comes from
  the paired phone, a channel injected text cannot also answer.
- **Count the asks.** If one kind is always approved, turn it into a standing grant with a limit, or drop it.

---

## 7. The one surface is the desktop

### 7.1 The idea

Exo has one surface, not a chat app plus a dashboard. It feels like a terminal: a prompt and a running
transcript, with `/` for commands, `@` to attach things, `!` for shell, and one palette. Richer elements
(diff view, task board, approval card, charts, file preview) are *panels* the model opens with a `ui` tool,
which sends a `ui.panel` event. Each client draws what it can (D-014, Exo `research/UI.md` §4).

In Exo OS that surface becomes the desktop itself. Note 02 found the key fact that makes this cheap: a panel
is just a view inside an Exo client, so the window manager never needs to know what a panel is (note 02 §1).

### 7.2 What it looks like

```
 +----------------------------------------------------------------------------+
 | > who do I need to call today?_                          EN  3:42 PM  ...  |  <- prompt bar (one line,
 +----------------------------------------------------------------------------+     drops down on a key)
 |                                                     | Today: 3 calls       |
 |                                                     | 1 Lead A (example)   |  <- drawer panel (opens
 |          WhatsApp, Gmail, CRM ...                   |   bid due Thu        |     only when it helps;
 |          (the app he was using, full size)          | 2 Lead B (example)   |     Esc closes it)
 |                                                     | 3 ...                |
 |                                                     |                      |
 |               +--------------------------------+    |                      |
 |               |  Send this message to Ahmed    |    |                      |  <- approval card (big,
 |               |  on WhatsApp?                  |    |                      |     plain words, Y or N)
 |               |  "Good morning, the quote ..." |    |                      |
 |               |        [ Yes ]   [ No ]        |    |                      |
 |               +--------------------------------+    |                      |
 +----------------------------------------------------------------------------+
```

Three shapes cover it: a one-line prompt bar at the top edge, a side drawer for panels, and a centred card
for approvals (note 02 §8). All three are *layer-shell* surfaces. Layer-shell is a Wayland feature that lets
a program draw bars and overlays above or below normal windows; it is how status bars and launchers work
(note 02 §2).

### 7.3 Choices made in note 02 that this design takes

- **Keep Hyprland as the window manager**, at least at first. It already runs, it already holds the test
  user's layout, and switching would cost a lot for no user gain. A custom compositor is years of work:
  COSMIC took about three years with a funded team (note 02 §2C and §9).
- **Write the surface as a protocol client** using gtk4-layer-shell, which works from Python and also covers
  the lock screen protocol (note 02 §2B). Keep Hyprland-specific parts to a small keybinding and window-rule
  file, so a move to niri or river later stays cheap (note 02 §9, point 6).
- **At most one web view, on demand,** for rich panels such as charts, closed when not in use. Every browser
  engine is a big memory cost (note 02 §7, note 04 §2).
- **Every panel has a text form.** It serves screen readers, the terminal client, and voice (note 02 §5).
  Orca and GTK4 on Wayland have known gaps, so test early (note 02 §5, RHEL-65520).
- **Hints before pop-ups.** A panel is first offered as a one-line hint ("5 things, show board?"). Whether to
  open it straight away is a decision-model call, and dismissals are logged as learning signal (D-014, D-015).
- **A way out.** If Exo fails, a plain desktop with the person's app keys still works. A shell with no exit
  can trap a user (note 02 §6, from the cage listing).

### 7.4 The palette is the launcher

On the test machine, Super+Space opens Omarchy's menu (local `~/Work/KEY-CARD.md`). In Exo OS the same key
drops the prompt bar. Typing "whats" offers WhatsApp; typing a question asks Exo. One key does both jobs, so
there is nothing new to learn.

---

## 8. Voice and phone are clients of the same protocol

The Exo protocol is commands in, events out, one ordered stream, and every event has a sequence number so a
client that drops off can resume where it left off (Exo `docs/STANDALONE.md` §5.1). Clients declare what they
can show (`diff`, `markdown`, `voice`, screen width), and the core shapes its output to match (Exo
`research/UI.md` §4.2). That is everything voice and phone need.

```
            +---------- desktop surface (full panels)
            |
   exod ----+---------- voice client: speech -> turn.send ; message.completed -> speech
            |                          approval.requested -> "Shall I send it? Say yes or no."
            |
            +---------- phone (paired by QR): cards, notifications, approve with a tap
            |                          also the second channel for the worst-class asks
            |
            +---------- bus channel: A and his agents reach the same core, signed
```

- **Voice** is Jarvis rebuilt as a protocol client. Jarvis today records while a key is held, transcribes
  with Whisper base (about 1.5 times real time on this machine's two cores, measured locally), answers, and
  speaks with online voices, falling back to Piper offline (local `~/Work/REPORT-04.md`). In Exo OS it becomes
  a thin client: the same session the desktop shows, so "and send that to Ahmed" works across the two.
- **Phone** pairs once by a one-time link or QR code (the pattern opencode uses, Exo `research/UI.md` §1). It
  gets approval requests as notifications, and still-open requests are replayed when it reconnects (a Hermes
  feature Exo lifts, Exo `research/UI.md` §3). It is also the out-of-band channel for money and deletes (§6.3).
- **The companion device** later is one more client with a tiny screen or none (D-009 horizon). Nothing new is
  needed in the protocol for it, which is the point of designing it this way now.

Principle check: Exo treats voice and low latency as low priority (P3, "rules out: voice and low-latency
features as a priority"). For a non-technical user who already talks to his laptop daily, voice is the main
door, not a feature. That tension is real and is listed in §13.

---

## 9. Many users, and a non-technical one

### 9.1 One principal per login

Each Linux account is one *principal* (the person Exo works for) with one store and one `exod`. A family
laptop with two accounts runs two cores that never share a store. This matches Exo's rule that the
mechanism is generic and everything about a person lives only in their store (D-004).

### 9.2 The operator role

Someone else often looks after a non-technical person's computer. On the test machine that is A (Ahraz),
who sets things up remotely through his own agents. Exo already separates *operator* rules, which protect
third parties and which the principal model cannot override, from the principal's own preferences (P11).
Exo OS makes the operator a named, authenticated role:

- The operator can propose and apply system changes through the same gate, from their own channel (the bus).
- Every operator change shows to the principal in plain words: "Ahraz made the text larger. Undo?"
- The operator cannot read the principal's private corpus or messages unless the principal grants it (guess at
  policy; A decides, see DECISIONS-PROPOSED.md D4 and D7).
- Reboots, logouts and anything that costs money stay the principal's call, as they are today.

### 9.3 What would make Shahid's day easier

Shahid co-owns Aska, sells carbon fiber strengthening products to contractors across ten US states, speaks
English, Urdu and Arabic, and is not technical. His tools are WhatsApp web, Gmail, Drive, Meet and a local
CRM of about a hundred companies. He wants plain short sentences, answers in the language he wrote in,
12-hour time, and to be asked before anything irreversible or costly (local `~/Work/ABOUT-SHAHID.md`).

Concrete things Exo OS should do for him, in rough order of value (guess at ranking):

1. **A morning card in one key.** "Who do I need to call today?" answered from the CRM's next-action dates,
   with a one-line reason for each, and a drafted WhatsApp or email ready for each that he can send with one
   Yes. Today Jarvis can already read his leads; it took 21 seconds to answer a CRM question by voice
   (local `~/Work/REPORT-04.md`).
2. **Call prep before a Meet.** When a meeting is near, a small card: who it is, the project, the last thing
   said, the bid date.
3. **Write in his language, send in theirs.** He dictates in Urdu; Exo drafts in English for an American
   contractor and shows both before he says Yes.
4. **"What did you do today?" has a plain answer.** A timeline panel built from the ledger: every message
   drafted, every change made, each with Undo where undo is possible.
5. **Things fix themselves, and say so.** If an update breaks the WhatsApp window, Exo rolls back to the last
   snapshot and tells him: "WhatsApp stopped working after an update, so I put things back. Ahraz knows."
6. **"Ask Ahraz" is one button.** It sends A a bus message with what happened and the relevant log lines, so
   Shahid never has to describe a technical problem.
7. **His own rules, enforced.** His "never" list (no money, no deletes, no settings changes without asking)
   becomes his `policies/gates.yaml`, so the gate holds it, not the model's memory of it.
8. **Nothing technical on screen.** No slash commands shown, no terminal, no model names. The prompt bar and
   his app keys are the whole interface. The rest is reachable for A, not visible to Shahid (Exo
   `research/UI.md` §4.1, "power is reachable, not visible").
9. **Big, readable asks.** Approval cards in large text, read aloud on request, in his language.

He is also the best test of the design. If a change makes his day harder, it does not ship (note 02 §10
exit rule).

---

## 10. How Exo OS stays one with Exo

A wants Exo OS to be Exo, not a copy that drifts. Exo learned this lesson the hard way: it chose a lean core
over a hard fork of Hermes because a fork "owns" a huge codebase that stops merging within weeks (D-010,
Exo `docs/STANDALONE.md` §2). The same rule applies one level up.

```
  aska-digital/exo  (the core)                    aska-digital/exo-os  (this repo)
  ------------------------------                  ------------------------------------
  exod, protocol, gate, store,       <-- pinned   image build (base, packages, boot)
  ledger, learning loop, seats,          release  exo-sysd (root helper, named actions)
  plugin API, broker (proposed)                   exo-os plugin (app, file, system tools)
                                                  desktop surface client
   ^                                              voice and phone clients (or in exo)
   |  anything the OS needs from the core         policies and seat templates for the OS
   +---- goes upstream as a change to Exo first   sandbox and network profiles
```

The rules:

1. **Exo is a dependency.** Exo OS installs a pinned Exo release. No copy of Exo's source lives here.
2. **Plugins and protocol only.** The exo-os plugin uses the public plugin API. Clients speak the public
   protocol. If either is not enough, the change goes to Exo first, and Exo OS waits for the release.
3. **OS-specific means Linux wiring.** systemd units, bubblewrap profiles, snapper calls, Hyprland files
   belong here. Ideas that make any Exo safer (the tool broker, capability grants, taint tracking) belong in
   Exo, so Exo on a Mac gets them too.
4. **Same store, same format.** A store created on Exo OS opens in plain Exo, and the other way round. The
   machine profile (§3.3) is just more nodes and policies in the same format.
5. **Contract tests.** Exo OS CI runs its plugin and clients against each new Exo release before the pin
   moves, the same way Exo once pinned Hermes (Exo `docs/STANDALONE.md` §3).

Things Exo OS will likely ask of Exo (guesses, to be confirmed with the Exo lead): the `ui.panel` event and
`ui` tool (already planned for Exo wave 3, D-014); system gate categories (§5.2) in the gate's table or as a
policy extension; a `principal` versus `operator` field on events; a client capability for "large text, plain
language".

---

## 11. Small hardware tiers

The test machine has 5.7 GB of RAM and an AMD Athlon Silver 3050U with two cores at up to 3.2 GHz
(note 04 §1). Two cores is the real limit: local models get slow before memory runs out (note 04 §1).

Where memory goes (all guesses from note 04 §2, to be measured): the kernel and services about 300 MB, the
compositor and shell about 300 MB, Chromium with his five web apps 1.5 to 2.5 GB, `exod` 150 to 400 MB,
leaving 1 to 2 GB. Chromium is the real cost, not the window manager.

| Tier | Example | Runs on the device | Goes remote |
|---|---|---|---|
| 0 Thin | small ARM board under 2 GB | exod core, wake word, rules, a queue | all models, speech |
| 1 Small (the test machine) | 3050U laptop 5.7 GB, Pi 5, old ThinkPad 8 GB | exod, desktop, web apps; Whisper base, Piper voices; a small embedder; optionally one model of 1.5B or less at 4-bit, loaded only when needed | reasoning, long documents, Arabic and Urdu speech quality, vision |
| 2 Mid | 16 GB laptop or RK3588 board | tier 1 plus a 3B to 4B model, Whisper small | hard reasoning, long context |
| 3 Home box | 32 GB mini PC or Jetson-class | 7B to 8B models, bigger speech models, shared store and sync for all devices | frontier reasoning |
| 4 Cloud | hosted APIs | nothing | everything hard |

Source: note 04 §12. Model sizes for tiers 2 and 3 are guesses there too.

Design rules that follow:

- **Local models are optional, separate processes** with a memory cap, started on demand and stopped when
  idle, never inside `exod` (note 04 §9).
- **Check free memory before loading anything.** Leave at least 1 GB free with Chromium open (note 04 §9).
- **Small decisions do not need a big model.** Routing, yes or no, "does this need approval" can be rules or
  tiny classifiers (note 04 §6). But anything that can break something is decided by rules or the person,
  never by a tiny model alone.
- **Decision role on tier 1.** Exo's default decision model is Jev, a remote API, with local Laya as fallback
  (D-019). Laya needs torch and several GB of downloads (Exo `docs/PROVIDERS.md`), which does not fit tier 1.
  So on tier 1: Jev remote, then rules. Guess: this is fine because the gate's floor is rules anyway.
- **Arabic and Urdu need testing early.** Small English-first models may fail there (note 04 §13).
- **A home box solves several problems at once:** heavy models, key storage away from a laptop that can be
  lost, and sync between devices (note 04 §10).

---

## 12. Lessons from those who tried before

From note 03, each lesson with what Exo OS does about it.

| Lesson (note 03) | Evidence there | What Exo OS does |
|---|---|---|
| 1. Be a layer on a normal OS, not a new device or kernel | Rabbit, Humane, 01 Light; Plan 9, Lisp machines | Arch base, normal apps, normal hardware (§1) |
| 2. Own the data so it outlives the vendor | Humane pins stopped working when servers stopped | the store is local, portable and plain files (§3.3) |
| 3. Privacy by default: opt in, encrypted, visible | Recall's default-on capture and weak storage; apps began blocking it | no screen recording by default; reading the screen happens only when asked; show when data leaves the machine (Jolla's idea) |
| 4. External content is untrusted data | Comet, Atlas, OpenClaw injection cases | reader/doer split, sandbox, egress control (§6) |
| 5. Gate by reversibility, not prompt count | 93% of prompts approved | system categories with real undo behind them (§5) |
| 6. Agents get their own scoped identity | Windows agent workspace; Claude Code sandbox | exod as the user, never root; exo-sysd named actions; broker (§3, §6) |
| 7. Structured tools before pixels | OSWorld scores; Rabbit's scripts (reported) | the order of doors (§4.1) |
| 8. Memory is the product; keep it reviewable | MemGPT, Hermes Agent, OS-Copilot | Exo's store, with beliefs as reviewed propositions (P9) |
| 9. Skills and plugins are code; vet them | OpenClaw's skill store carried malware | pinned, hashed, sandboxed skills and MCP servers; alert on change (note 05 L4) |
| 10. Narrow, swappable, local-first parts | Home Assistant voice pipeline | voice and models as separate swappable processes (§8, §11) |

One more from note 03 §F: much of the Linux desktop community is wary of AI right now, and several projects
restrict AI-made contributions. Exo OS should be honest about what it is, and an easy way back to a plain
desktop should always exist (guess, from that section).

---

## 13. Open questions

1. **Home as store.** Should normal documents live inside the store, or stay in normal folders the store
   indexes? (§3.3) A spike with Shahid's Downloads folder would answer it.
2. **Rollback that needs a reboot.** Reboots are the person's call, but a broken package may need one to
   undo. Does Exo stage the undo and ask, or is there a safe live path? Note 01 §9 open question 1.
3. **Voice versus P3.** Exo ranks voice low; for Shahid it is the main door. Does Exo OS overrule P3 for some
   principals, or is this a per-principal setting? Needs A.
4. **Operator reach.** How much of Shahid's store can A's agents read when helping him? (§9.2)
5. **Orca and GTK4 layer-shell on Hyprland.** Untested (note 02 §11).
6. **Real numbers.** Every RAM and speed figure for this machine except Whisper's is a guess. Measure PSS of
   each piece with Chromium open, and llama.cpp speed for one 1.5B model (note 04 §13).
7. **WhatsApp.** Exo plans to lift a WhatsApp adapter from Hermes (Exo `docs/STANDALONE.md` §1.2). How that
   adapter connects, and whether it suits a personal account, is not checked here. Until then, WhatsApp is
   reached through its web window (door 3 and 5 in §4.1).
8. **Multilingual embeddings.** D-011 chose EmbeddingGemma 300M locally; its speed on this CPU and its Urdu
   quality are untested (note 04 §4 lists the multilingual need as open).
9. **Base system later.** Does `exod` run unchanged as a NixOS module or a bootc image? Note 01 §9 says keep
   distro actions behind one adapter so this stays cheap.
10. **Where the signer lives.** A separate system user, exo-sysd, or the TPM (note 05 §5)?

---

## Sources

Research notes in this repo: `research/01-base-system.md`, `02-shell-and-compositor.md`, `03-prior-art.md`,
`04-small-hardware.md`, `05-security.md`. Each lists its own external links; the main ones used here:

- Omarchy snapshots: https://raw.githubusercontent.com/basecamp/omarchy/master/manual/47-system-snapshots.md
- snap-pac: https://github.com/wesbarnett/snap-pac
- NixOS manual: https://nixos.org/manual/nixos/stable/
- gtk4-layer-shell: https://github.com/wmww/gtk4-layer-shell
- COSMIC timeline: https://lwn.net/Articles/1050268/
- river 0.4: https://linuxiac.com/river-0-4-wayland-compositor-debuts-pluggable-window-managers/
- Anthropic on approval rates: https://anthropic.com/engineering/claude-code-auto-mode
- Anthropic computer use: https://www.anthropic.com/news/3-5-models-and-computer-use
- Willison on agentic browser security: https://simonwillison.net/2025/Aug/25/agentic-browser-security/
- Claude Code sandboxing: https://code.claude.com/docs/en/sandboxing
- systemd credentials: https://systemd.io/CREDENTIALS/
- Landlock: https://man.archlinux.org/man/landlock.7
- Humane: https://techcrunch.com/2025/02/18/humanes-ai-pin-is-dead-as-hp-buys-startups-assets-for-116m
- Windows Recall: https://en.wikipedia.org/wiki/Windows_Recall

Exo documents (in the Exo repo): `DECISIONS.md` (D-004, D-009 to D-021), `research/PRINCIPLES.md`,
`research/UI.md`, `docs/STANDALONE.md`, `docs/CORE.md`, `docs/ARCHITECTURE.md`, `docs/PROVIDERS.md`,
`docs/SEATS.md`, `docs/STORE-V2.md`, `src/exo/gate.py`.

Local facts about the test machine and its user: `~/Work/CLAUDE.md`, `~/Work/ABOUT-SHAHID.md`,
`~/Work/KEY-CARD.md`, `~/Work/REPORT-04.md` (Jarvis, including the one local speed measurement used here).

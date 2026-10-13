# Exo OS: decisions A must make

**Ruled on 2026-10-12: see `DECISIONS.md` (OS-1 to OS-9). This file is kept as the record of the options.**

Date: 2026-10-12. Status: ruled on 2026-10-12 (see `DECISIONS.md`). The text below is the proposal as it
was put, with options, the trade and a recommendation. Numbering here (D1 to D8) is local to Exo OS. The
rulings are recorded in this repository's `DECISIONS.md`, not in Exo's, since Exo OS is its own repository.

Needed before phase 1: D1, D3, D8. The rest can wait for the phase that needs them (ROADMAP.md).

---

## D1. Base system

How each option makes changes reversible is the main criterion (note 01, weighted heavily by the brief).

| Option | Trade |
|---|---|
| a. Own Arch image with btrfs, snapper, snap-pac and Limine, using Omarchy's pieces as a reference | Least work; already proven on this machine; fits 5.7 GB. Undo covers the system only, not home; rolling updates can break things, and a snapshot is the only net (note 01 §2). |
| b. Fork Omarchy | Fastest to a finished desktop, MIT licence allows it. But you track a fast-moving upstream with a young Lua config, and Exo is meant to replace it (note 01 §5). |
| c. NixOS | Best agent loop: the whole system is a text file; build, test, switch, roll back; built-in VM builds. Costs: the Nix language, and builds that can need a lot of memory (one report near 12 GB on a first build) unless built elsewhere (note 01 §3). |
| d. Fedora Atomic, bootc or Universal Blue images | Read-only base, signed images, clean fleet updates. Slow change loop (each system change is an image build) and a move to the RPM world (note 01 §4). |
| e. Immutable Arch variants or Guix | Attractive ideas; small projects with longevity risk, or a smaller ecosystem (note 01 §6). |

**Recommendation: (a) now, and keep `exod` and exo-sysd free of Arch-only assumptions** (all distro actions
behind one adapter), so (c) or (d) can be tested in phase 8 for shipping to others (note 01 §9). The value of
Exo OS is the gate, the log and the store, not the package manager.

## D2. Shell approach

| Option | Trade |
|---|---|
| a. Keep Hyprland; Exo surface as a layer-shell client (Python GTK4) | Low effort; apps keep working; the window manager does not need to know about panels. Hyprland's config API is still moving (note 02 §2A, §9). |
| b. Same, with Quickshell (QML) instead of GTK4 | Richer shell toolkit; still alpha, breaking changes expected (note 02 §2A). |
| c. Custom compositor (Smithay, river window manager) | Full control. Years of work; accessibility, screen sharing and HiDPI become ours. COSMIC took about three years with a team (note 02 §2C). |
| d. Kiosk (cage) | Tiny and stable; one app only, so it breaks the web apps Shahid needs. Good later for a device (note 02 §2D). |
| e. Web-tech shell (Electron, Tauri) as the desktop | Easiest rich panels; a second browser engine next to Chromium costs memory (note 02 §2E). |

**Recommendation: (a), with one on-demand web view only for rich panels.** Pick GTK4 or Quickshell at the
phase 3 gate by measured memory and Orca behaviour (note 02 §10). Keep Hyprland-specific code to one small
file so niri or river stay open.

## D3. Where it first runs

| Option | Trade |
|---|---|
| a. Charminar directly | Real user at once. Risks Shahid's working machine (CRM, WhatsApp, Jarvis), which the brief rules out for now. |
| b. VM only, for a long time | Safe and cheap. Never meets real hardware limits, real drivers or a real user. |
| c. Staged: VM, then a spare laptop like charminar, then a USB trial on charminar, then install beside Omarchy | Safe at each step, and meets the real user. Needs one spare laptop and more time. |

**Recommendation: (c).** The VM host should be x86 (guess: Arch's main project is x86; note 01 §2). See
ROADMAP.md phases 1, 6 and 7.

## D4. How system changes get root

| Option | Trade |
|---|---|
| a. Today's approach: a list of exact sudo commands in sudoers | Works, but it is a list of commands, not of meanings; a password is still needed for snapshots on this machine (note 01 §5, local memory file). |
| b. A small root helper (exo-sysd) with named actions only: snapshot, apply a listed change, check, rollback | Narrow door; each action logs itself; no general root shell. More code to write and test (note 01 §8, point 4). |
| c. The agent runs as root, or has full sudo | Simple. Unacceptable: one fooled model can break the machine (note 05 §1). |
| d. Declarative only (NixOS-style): the agent edits a config, a human runs the switch | Very safe; slow, and needs a person at the keyboard for every change. |

**Recommendation: (b).** Reboots stay the person's call; bootloader, partitions and firmware are never on
the named list.

## D5. Local model scope on small machines (tier 1)

| Option | Trade |
|---|---|
| a. Everything remote except speech and rules | Best quality, cheapest to build. Needs a network; private text goes to providers. |
| b. Speech, voice, a small embedder and rules local; reasoning and decisions remote; one small model (1.5B or less, 4-bit) on demand for offline only | Private and offline for the simple things; fits the memory left after Chromium (note 04 §2, §13). Two-core speed is unmeasured for the model (guess 3 to 8 tokens per second, note 04 §3). |
| c. Fully local | Most private. Does not fit 5.7 GB with a browser open at useful quality (note 04 §7). |

**Recommendation: (b).** Whisper base and Piper already run here (Whisper at about 1.5 times real time,
local `~/Work/REPORT-04.md`). The decision role stays Jev remote with rules as fallback, since Laya needs
torch and several GB (D-019, Exo `docs/PROVIDERS.md`). Measure before adding anything bigger.

## D6. Secrets

| Option | Trade |
|---|---|
| a. `keys.env` file (Exo today, mode 0600) | Simple. Any process of the user, including a fooled tool, can read it (note 05 §1.2). |
| b. Desktop keyring (Secret Service) | Familiar, unlocked at login. Any same-user process can usually ask for a secret (note 05 §4, marked from memory there). |
| c. systemd-creds (TPM where present) feeding a local broker that injects credentials into outbound calls | The model and its tools never hold a key; protects at rest too. More moving parts; the broker products in note 05 are self-described, not audited (note 05 §4). |

**Recommendation: (c),** with the broker proposed to Exo as a core mechanism so Exo on any machine gains it.

## D7. User scope

| Option | Trade |
|---|---|
| a. A only, as the first principal | Easiest feedback loop. Misses the hardest and most useful test: a non-technical person. |
| b. One principal per machine, plus an operator role (A helping Shahid) | Matches the real situation; needs clear rules for what the operator may see and do (DESIGN.md §9.2). |
| c. Full multi-user household from the start | General. More policy and testing before anything ships. |

**Recommendation: (b),** with Shahid as the first real principal and A as operator. Operator changes always
show to the principal in plain words. **A must also rule:** may A's agents read Shahid's messages and store
contents when helping, or only system state and logs?

## D8. Naming and licence

| Option | Trade |
|---|---|
| a. "Exo OS", MIT like Exo, repo private until phase 6 | Same licence as Exo, Omarchy and Hermes lifts (Exo `docs/STANDALONE.md` §1.3; note 01 §5), so parts move freely. Others may reuse it without giving back. |
| b. "Exo OS", copyleft (GPL family) | Forces changes to stay open. Mixed licences between Exo and Exo OS complicate moving code up into Exo. |
| c. A separate product name | Freedom to market it apart. Breaks "Exo OS is Exo" in people's minds. |

**Recommendation: (a).** Keep the image's component names visible (Arch, Hyprland, Chromium) and check each
component's licence before any public image (not checked in this phase). Commit messages and public text
carry no agent or team names (local `~/Work/CLAUDE.md`, rule 6).

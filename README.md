# exo-os

Exo OS: an OS form of Project Exo. It replaces a machine's stock desktop distribution (Omarchy on the first
test machine, but not only Omarchy) with an AI-first operating system built around one person. It takes
Exo's ideas to their peak: the signed log as the source of truth, gates by reversibility, a portable store of
the person, and one calm surface.

Exo OS stays one with Exo: same core, same protocol, no fork. Exo is a dependency, not a copy. Anything the OS
needs from the core goes to Exo first.

## Status

Phase 0: research and design only. Nothing is built. Nothing on any machine was changed. Where the OS first
runs is A's decision (see `DECISIONS-PROPOSED.md`, D3).

## Where things are

| Path | What it is |
|---|---|
| `DESIGN.md` | The architecture, each idea explained from first principles, with diagrams and open questions |
| `ROADMAP.md` | Phases from a bootable VM image to daily use, each with an acceptance check and what it needs from A |
| `DECISIONS-PROPOSED.md` | Eight decisions for A: options, trade, recommendation |
| `research/01-base-system.md` | Base system options and how each makes changes reversible |
| `research/02-shell-and-compositor.md` | Desktop shell and window manager options; the one surface as a desktop |
| `research/03-prior-art.md` | Earlier AI-first OS attempts and agent desktops, and lessons |
| `research/04-small-hardware.md` | What runs on 5.7 GB and two cores; local models; device tiers |
| `research/05-security.md` | Sandboxing, approvals, secrets, audit log for an agent with OS reach |
| `notes/site.md` | Running list of things to show on the future exo.askaconsult.com/os page |

## Main recommendations (phase 0)

- Base: an Arch image with btrfs snapshots, using Omarchy's pieces as a reference, not a fork.
- Desktop: keep Hyprland; the Exo surface is a layer-shell client of the Exo protocol.
- `exod` runs as the user, never as root; one small root helper does only named, logged, reversible actions.
- Every system change: snapshot, apply, check, and put back by itself if the check fails.
- Voice and phone are clients of the same protocol.
- First real user: a non-technical person, with A as operator.

## Related

- Exo core: aska-digital/exo (the dependency). Exo's D-020 records this branch.

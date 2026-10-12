# exo-os

Exo OS: an OS form of Project Exo. It replaces a machine's stock desktop distribution (Omarchy on the first
test machine, but not only Omarchy) with an AI-first operating system built around one person. It takes
Exo's ideas to their peak: the signed log as the source of truth, gates by reversibility, a portable store of
the person, and one calm surface.

Exo OS stays one with Exo: same core, same protocol, no fork. Exo is a dependency pinned by commit
(`exo.pin`), not a copy. Anything the OS needs from the core goes to Exo first.

## Status

Phase 1 (a bootable image) is in progress on branch `phase-1`. Built and tested so far:

- **Image.** `image/` builds an Arch disk image with mkosi, unprivileged: btrfs subvolumes for the system,
  home, snapshots and the Exo store; Hyprland; Chromium; Exo and exo-os installed at the pinned commit;
  `exod` as a user service; exo-sysd behind a socket; automatic login to the desktop.
- **Change engine.** A machine profile (`machine.toml`) is the one file that says how the desktop should be.
  Every change is validated, rendered to Hyprland and hypridle config, checked, committed to a git repo in
  the store, and written to the person's signed Exo ledger. If a check fails it is put back. Undo is a
  logged change.
- **Exo plugin.** Tools that let the agent show and change the machine, open apps, list changes, undo, and
  install packages (which asks first), placed by Exo's own gate.
- **exo-sysd.** The one root process: four named actions, signed receipts.
- **Surface.** A GTK4 layer-shell prompt bar, panel drawer and approval card, as an Exo protocol client
  (being built).

Not yet: rollback that boots an older snapshot, the sandbox and secrets layers, voice and phone clients.
See `ROADMAP.md`.

## Use

    tools/dev-env.sh                       # .venv with Exo at exo.pin and exo-os, editable
    .venv/bin/pytest                       # the tests
    tools/host-tools.sh                    # mkosi, QEMU and OVMF into ~/.local/opt/exo-os-tools (no root)
    tools/build-image.sh --profile=vmtest  # build image/out/exo-os.raw
    tools/run-vm.sh [--gui]                # boot it in QEMU (throwaway overlay; SSH on 127.0.0.1:2222)
    tools/vm-ssh.sh <command>              # run a command in the VM as root
    tools/accept-phase1.sh                 # the phase 1 acceptance checks

On a machine running Exo OS (or any machine with Exo):

    exo-os show                            # the machine profile
    exo-os set keyboard.layouts us,ara,pk  # one change: checked, logged, undoable
    exo-os history                         # what changed, newest first, and who changed it
    exo-os undo [change]                   # put back the latest change, or the one named
    exo-os app add WhatsApp --url https://web.whatsapp.com/ --key SUPER+1 --workspace 1

## Where things are

| Path | What it is |
|---|---|
| `DESIGN.md` | The architecture, each idea explained from first principles |
| `DECISIONS.md` | Decisions made, why, and what was rejected (OS-1 onward) |
| `ROADMAP.md` | Phases from a bootable VM image to daily use, each with an acceptance check |
| `src/exo_os/profile.py` | The machine profile: fields, checks, apps |
| `src/exo_os/render/` | Profile to Hyprland Lua and hypridle config |
| `src/exo_os/changes.py` | The change engine: apply, check, commit or put back, undo |
| `src/exo_os/plugin.py` | The Exo plugin (tools for the agent) |
| `src/exo_os/sysd/` | exo-sysd, the root helper, and its client |
| `src/exo_os/surface/` | The desktop surface (prompt bar, drawer, approval card) |
| `image/` | The mkosi image: packages, partitions, units, base desktop config |
| `tools/` | Build, boot and acceptance scripts |
| `research/` | Phase 0 research notes |
| `notes/site.md` | Things to show on the future exo.askaconsult.com/os page |

## Related

- Exo core: aska-digital/exo (the dependency). Exo's D-020 records this project.

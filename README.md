# exo-os

Exo OS: an OS form of Project Exo. It replaces a machine's stock desktop distribution (Omarchy on the first
test machine, but not only Omarchy) with an AI-first operating system built around one person. It takes
Exo's ideas to their peak: the signed log as the source of truth, gates by reversibility, a portable store of
the person, and one calm surface.

Exo OS stays one with Exo: same core, same protocol, no fork. Exo is a dependency pinned by commit
(`exo.pin`), not a copy. Anything the OS needs from the core goes to Exo first.

## Status

The initial phases 1–3 passed their earlier VM checks, with the gaps in `ROADMAP.md`.
The current runtime adds recoverable approvals, authenticated root receipts, compulsory isolated tool
execution and administrator boot recovery. A fresh image is being validated; earlier VM results are
not evidence for this revision. Built:

- **Image.** `image/` builds an Arch disk image with mkosi, unprivileged: btrfs subvolumes for the system,
  home, snapshots, the Exo store and persistent helper identity/audit; Hyprland; Chromium; Exo and exo-os installed at the pinned commit;
  `exod` as a user service; exo-sysd behind a socket; automatic login to the desktop.
- **Change engine.** A machine profile (`machine.toml`) is the one file that says how the desktop should be.
  Every change is validated, rendered to Hyprland and hypridle config, checked, committed to a git repo in
  the store, and written to the person's signed Exo ledger. If a check fails it is put back. Undo is a
  logged change.
- **Exo plugin.** Tools that let the agent show and change the machine, open apps, list changes, undo, and
  install packages (which asks first), placed by Exo's own gate.
- **exo-sysd.** The one root process: five named actions (status, snapshot create, list and undo,
  package install), kernel-authenticated peers, independently verified receipts, and reconciliation of
  receipts that could not be written to the principal ledger.
- **Surface.** A GTK4 layer-shell prompt bar, panel drawer and approval card, as an Exo protocol client,
  with approval cards in plain words. Escape defers; lost answers are reconciled without automatic retry.
- **Timeline.** `exo-os today` and the `timeline_show` tool: what Exo did on a day, from the ledger.
- **Tool boundary.** Every registry call passes through the required executor. Workspace tools run
  under bubblewrap, Landlock and seccomp with no network, credentials or host control sockets.
  Unsupported adapters refuse execution. Reviewed desktop operations use structured arguments.
- **Boot recovery.** An administrator can checkpoint a system together with its matching kernel image
  and stage a separate recovery boot. Home, the store and the helper's identity/audit persist.

Still required: current-image boot/recovery acceptance, credential-injecting egress, a separate core
ledger signer and off-machine anchor, voice and phone clients, and the hardware trials in `ROADMAP.md`.
Isolation covers model tool processes. Installed providers/plugins and the person's other desktop
applications remain trusted; this is not a claim of separation between every same-user process.
See [the core/OS contract](docs/CORE-CONTRACT.md) and [tool isolation](docs/TOOL-ISOLATION.md).

## Use

    tools/dev-env.sh                       # .venv with Exo at exo.pin and exo-os, editable
    .venv/bin/pytest                       # the tests
    tools/test-linux.sh                    # Linux socket + GTK tests in a disposable Docker container
    tools/host-tools.sh                    # mkosi, QEMU and OVMF into ~/.local/opt/exo-os-tools (no root)
    tools/build-image.sh --profile=vmtest  # build image/out/exo-os.raw
    tools/run-vm.sh [--gui]                # boot it in QEMU (throwaway overlay; SSH on 127.0.0.1:2222)
    tools/vm-ssh.sh <command>              # run a command in the VM as root
    tools/accept-phase1.sh                 # the phase 1 acceptance checks
    tools/accept-isolation.sh              # kernel isolation in a disposable booted image
    tools/accept-recovery.sh               # checkpoint, reboot into recovery and verify persistence

On a machine running Exo OS. On any other desktop, `exo-os` changes nothing there and says so; the Exo store
still works. The image explicitly marks its desktop with
`~/.config/hypr/exo/desktop-owner` containing `exo-os-v1`, alongside the profile include. A comment
mentioning Exo is not an ownership marker.

    exo-os show                            # the machine profile
    exo-os set keyboard.layouts us,ara,pk  # one change: checked, logged, undoable
    exo-os history                         # what changed, newest first, and who changed it
    exo-os undo [change]                   # put back the latest change, or the one named
    exo-os system reconcile                # record saved root receipts; never rerun the actions
    exo-os app add WhatsApp --url https://web.whatsapp.com/ --key SUPER+1 --workspace 1

## Where things are

| Path | What it is |
|---|---|
| `DESIGN.md` | The architecture, each idea explained from first principles |
| `DECISIONS.md` | Decisions made, why, and what was rejected (OS-1 onward) |
| `ROADMAP.md` | Phases from a bootable VM image to daily use, each with an acceptance check |
| `docs/CORE-CONTRACT.md` | Principles as acceptance conditions; shared-core integration requirements |
| `docs/SYSTEM-TRUST.md` | Root authentication, signed outcomes and audit recovery |
| `docs/SURFACE-RECOVERY.md` | Approval recovery, connection health and protocol limits |
| `docs/TOOL-ISOLATION.md` | Compulsory execution, working set and trust limits |
| `docs/BOOT-RECOVERY.md` | Administrator checkpoints and matching-kernel recovery |
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

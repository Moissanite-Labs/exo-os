# exo-os

Exo OS: an OS form of Project Exo. It replaces a machine's stock desktop distribution (Omarchy on the first
test machine, but not only Omarchy) with an AI-first operating system built around one person. It takes
Exo's ideas to their peak: the signed log as the source of truth, gates by reversibility, a portable store of
the person, and one calm surface.

Exo OS stays one with Exo: same core, same protocol, no fork. Exo is a dependency pinned by commit
(`exo.pin`), not a copy. Anything the OS needs from the core goes to Exo first.

**Start here: [`docs/BANKAI.md`](docs/BANKAI.md).** A's direction for Exo OS. If the Exo harness is the first
release of Exo's spirit, Exo OS is the full release: the same principles, enforced by the whole machine instead of by
one process. It lists the invariants the OS must hold, maps each system from harness to OS, names the seams only the
OS can close, and lays out the build path. Read it before planning a stage.

## Status

The initial phases 1–3 passed their earlier VM checks, with the gaps in `ROADMAP.md`.
The current runtime adds recoverable approvals, authenticated root receipts, compulsory isolated tool
execution and administrator boot recovery. This new source composition pins merged core
main `5f434b1`; no image build or native acceptance is claimed for it. The separate
land/batch-2 source `4aec0b9` with core `3ea58f4` passed 545 Linux host tests with
12 skipped. Earlier image results do not transfer. Historical image evidence is
recorded in [validation](docs/VALIDATION.md#current-image-evidence).
The next composition's recovery flow has eleven checks, including a second reboot
back to normal. That flow is unrun in a VM. Earlier ten-check evidence is historical only.

Frozen image `c2d1ad9` with core `cff62d1` is built and audited. Its acceptance
is held: phase 3 failed both attempts and no complete native suite has passed.
The other six suites and witness cases are not run. Built:

- **Image.** `image/` builds an Arch disk image with mkosi, unprivileged: btrfs subvolumes for the system,
  home, snapshots, the Exo store, protected ledger, helper audit and egress history; Hyprland; Chromium; Exo and exo-os installed at the pinned commit;
  `exod` as a user service; exo-sysd behind a socket; automatic login to the desktop.
- **Change engine.** A machine profile (`machine.toml`) is the one file that says how the desktop should be.
  Every change is validated, rendered to Hyprland and hypridle config, checked, committed to a git repo in
  the store, and written to the person's signed Exo ledger. If a check fails it is put back. Undo is a
  logged change.
- **Exo plugin.** Tools that let the agent show and change the machine, open apps, list changes, undo, and
  install packages (which asks first), placed by Exo's own gate.
- **exo-sysd.** The privileged action helper: five named actions (status, snapshot create, list and undo,
  package install), kernel-authenticated peers, independently verified receipts, and reconciliation of
  receipts that could not be written to the principal ledger.
- **Surface.** A GTK4 layer-shell prompt bar, panel drawer and approval card, as an Exo protocol client,
  with keyboard-accessible panels and recent loaded history, plus full selectable approval details.
  Pending requests preserve typing; **Review request (F6)** enters keyboard review.
  The review button stays outside Tab completion navigation. Mouse review remains available.
  Escape defers; losing review focus revokes keyboard consent; lost answers are reconciled without automatic retry.
  The launcher probes GL and falls back to Cairo when it fails, and logs its choice (OS-20); real hardware is not yet checked.
- **Timeline.** `exo-os today` and the `timeline_show` tool: what Exo did on a day, from the ledger.
- **Tool boundary.** Every registry call passes through the required executor. Workspace tools run
  under bubblewrap, Landlock and seccomp with no network, credentials or host control sockets.
  Unsupported adapters refuse execution. Reviewed desktop operations use structured arguments.
- **Boot recovery.** An administrator can checkpoint a system together with its matching kernel image
  and stage a separate recovery boot. Home, the store, signer identity/history, helper audit and egress reservations persist.
- **Credentialed actions.** A separate service sends approved HTTPS requests to administrator-defined
  destinations. The full outgoing request is inspectable in the approval card; keys stay in the service.
  Durable request IDs prevent an uncertain action from being sent again. The shipped policy grants no destinations.
- **Protected history.** A separate service owns ledger keys and committed history. Fresh provisioning
  precedes login; missing established state refuses startup. Normal verification detects an altered or
  shortened desktop copy without repairing it. Explicit synchronization recovers committed rows.
  Writer attribution trusts the desktop account: processes running as `exo` can request signatures
  for any configured writer within its allowed author list. Separate writer keys do not isolate them.
  The one exception is the `principal` writer, which signs only for the process the core's launch record
  names (OS-21), though same-user code outside the tool box can make a record name itself. Other writers
  may still attribute events to the principal (proposed OS-22).
- **Model spend.** A root policy and persistent exo-owned journal start with zero daily/monthly paid
  ceilings and no priced routes. The offline fake model remains usable. Missing or altered spend state
  blocks login; status stays readable after an accounting stop. See [model spend](docs/MODEL-SPEND.md).

Still required: fresh-image acceptance, real-model chat, loaded-web-app memory,
native accessibility and Urdu visual checks, general API response adapters, independent
witness deployment, voice and phone clients, and the hardware trials in `ROADMAP.md`.
The current source pins merged core main `5f434b1`, which includes witness transport
and the challenged-head API, and adds optional explicit administrator
[witness delivery](docs/WITNESS-PUBLISHER.md). Enrollment remains disabled by default.
An independently administered endpoint remains unconfigured. Installed witness
Linux acceptance is pending.
Isolation covers model tool processes. Installed providers/plugins and the person's other desktop
applications remain trusted; this is not a claim of separation between every same-user process.
See [the core/OS contract](docs/CORE-CONTRACT.md) and [tool isolation](docs/TOOL-ISOLATION.md).

## Use

    tools/dev-env.sh                       # .venv with Exo at exo.pin and exo-os, editable
    .venv/bin/pytest                       # the tests
    tools/test-linux.sh                    # Linux socket + GTK tests in a disposable Docker container
    tools/host-tools.sh                    # mkosi, QEMU and OVMF into ~/.local/opt/exo-os-tools (no root)
    tools/build-image.sh --profile=vmtest  # build the acceptance image; omitting this builds production
    tools/run-vm.sh [--gui]                # boot it in QEMU (throwaway overlay; SSH on 127.0.0.1:2222)
    tools/vm-ssh.sh <command>              # run a command in the VM as root
    tools/accept-phase1.sh                 # the phase 1 acceptance checks
    EXO_OS_VM_ACCEPTANCE=1 tools/accept-isolation.sh --vm-acceptance --image-source <full-built-OS-commit>
                                            # kernel isolation with separate image/harness provenance
    tools/accept-ledger.sh                 # protected signer, tamper detection and signer outage
    EXO_OS_VM_ACCEPTANCE=1 tools/accept-model-spend.sh --vm-acceptance --image-source <full-built-OS-commit>
                                            # zero-paid offline model and journal checks
    tools/accept-recovery.sh               # recovery and return-to-normal reboots; eleven checks

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
| `docs/BANKAI.md` | The guiding document: why Exo OS is the full release of Exo, its invariants, systems, seams and build path |
| `DESIGN.md` | The architecture, each idea explained from first principles |
| `DECISIONS.md` | Decisions made, why, and what was rejected (OS-1 onward) |
| `ROADMAP.md` | Phases from a bootable VM image to daily use, each with an acceptance check |
| `docs/CORE-CONTRACT.md` | Principles as acceptance conditions; shared-core integration requirements |
| `docs/SYSTEM-TRUST.md` | Root authentication, signed outcomes and audit recovery |
| `docs/SURFACE-RECOVERY.md` | Approval recovery, connection health and protocol limits |
| [`docs/SURFACE-ACCESSIBILITY.md`](docs/SURFACE-ACCESSIBILITY.md) | Keyboard access, readable approval details and remaining screen-reader validation |
| `docs/TOOL-ISOLATION.md` | Compulsory execution, working set and trust limits |
| `docs/BOOT-RECOVERY.md` | Administrator checkpoints and matching-kernel recovery |
| `docs/CREDENTIAL-EGRESS.md` | Approved HTTPS actions, key isolation and uncertain outcomes |
| `docs/LEDGER-AUTHORITY.md` | Protected append history, first boot and independent witness handoff |
| `docs/MODEL-SPEND.md` | Zero-paid boot policy, journal persistence and trust limits |
| `docs/WITNESS-PUBLISHER.md` | Optional checkpoint publication, cached/fresh receipts and trust limits |
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

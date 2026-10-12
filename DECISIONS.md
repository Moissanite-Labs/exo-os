# Exo OS decisions

Every significant choice, why, and what was rejected. Numbering is local to Exo OS (OS-1, OS-2, ...);
`DECISIONS-PROPOSED.md` holds the phase 0 options these rule on. Exo's own decisions are cited as D-0NN.

Exo OS is now its own repository, as Exo's whitepaper §27 and docs map say, so these are not recorded in
Exo's `DECISIONS.md` (where D-021 to D-033 are taken and every heading needs a docs-map row).

---

## OS-1. Base: own Arch image built with mkosi (rules on proposed D1)

**Decision.** Arch base, btrfs with subvolumes `@`, `@home`, `@snapshots`, `@store`, snapper and snap-pac,
built from text config with mkosi (`image/`). Omarchy is a reference, not a fork. Distribution actions stay
behind exo-sysd's named actions, so NixOS or bootc can be tried later (phase 8).

**Why.** Proposed D1 (a). mkosi adds a property the proposal did not have: the whole image is a reviewed
text file, built unprivileged, and booted in QEMU by a script, which is the closest Arch gets to NixOS's
build, test, switch loop.

**Changed from the design.** Phase 1 boots with systemd-boot and a unified kernel image, because mkosi
supports it directly. Booting into an older snapshot from the boot menu (Limine plus limine-snapper-sync, as
Omarchy does) is deferred to phase 2, where rollback is built.

## OS-2. Shell: Hyprland plus a GTK4 layer-shell client (rules on proposed D2)

**Decision.** Keep Hyprland 0.56 (Lua config). The surface is a Python GTK4 client on gtk4-layer-shell that
speaks Exo's protocol. Quickshell is not used.

**Why.** gtk4-layer-shell 1.3 and PyGObject are packaged in Arch and already present on the test machine,
so no new toolkit is needed. The phase 3 memory and Orca checks can still reverse this.

## OS-3. Where it runs first: staged; images are built and booted in user space (rules on proposed D3)

**Decision.** Staged path as proposed: VM, then a spare laptop, then a USB trial on charminar, then install
beside Omarchy. The image is built and the VM run unprivileged in a user prefix (`tools/host-tools.sh`), with
nothing installed system-wide.

**Note.** For phase 1 the VM host has been charminar, at low priority while it is not in use, because it is
the only x86 machine available to this project. This changes no system file, package or setting on it. The
VM takes up to 3 GB of RAM while it runs, so it is not run while Shahid works. A host of A's (or a CI runner
with KVM) should take this over.

## OS-4. Root: exo-sysd with named actions only (rules on proposed D4)

**Decision.** One root service, `exo-sysd`, socket-activated on `/run/exo-sysd.sock` (group `exo-sys`, mode
0660). It does four things: `status`, `snapshot.create`, `snapshot.list`, `package.install`. Arguments are
checked, commands are fixed argv lists, never shell strings. Bootloader, partitions, firmware, kernel
options and reboots are not on the list.

**Each action returns a receipt signed with exo-sysd's own key** (`/var/lib/exo-sysd/sysd.key`), and the
caller writes it into the principal's ledger. The record of a root action is signed by the process that did
it, not by the agent that asked. This is a first step to the separate signer in DESIGN.md §5.4.

## OS-5. Most changes need no root at all

**Decision.** Everything a person changes day to day (text size, keyboard languages, idle times, app keys,
theme) is user-level config, so it goes through the change engine as the person, with git as the undo, not
through exo-sysd and snapper. Root and system snapshots are only for packages and system services.

**Why.** It keeps the root door small, and a user-level change applies and reverts live, with no reboot.

## OS-6. The machine repo

**Decision.** The machine profile and every file rendered from it live in a git repo at `<store>/machine`
(`machine.toml`, `rendered/...`), not at `<store>/policies/machine.toml` as the design guessed. Each change is
one commit with the person or agent who made it; each step is also a signed ledger row (`kind=action`,
`body.os=exo-os`, phases `change.proposed`, `change.applied`, `change.reverted`, `system.action`).

**Why.** Exo's `policies/` holds gate and budget policies the gate reloads; the machine profile is not one,
and one git repo gives a readable diff and history for free. Exo's ledger kinds are a closed set
(`src/exo/ledger.py`), so Exo OS uses `action` with a phase, as the gate does.

## OS-7. Use Exo's gate as it is: tool names, risk and policy, no new categories

**Decision.** Exo's gate cannot take categories from a plugin (`gate.py` `classify`), and the protocol has
no free command slots. So exo-os tools are named so the gate's own rules place them:
`machine_set` and `machine_app_put` are `local_write` (reversible, run), `machine_show` and
`machine_changes_list` are `read`, `system_package_install` has `risk="high"` and is raised to ask.
Per-principal rules go in `policies/gates.yaml`. The `sys_*` categories in DESIGN.md §5.2 remain a
proposal to Exo, not a dependency.

## OS-8. Local models, secrets, user scope (rules on proposed D5, D6, D7)

- **D5:** (b) as proposed. Nothing local is built yet; measure before adding.
- **D6:** (c) as the target. Until it is built, Exo's Linux path stands: `~/.config/exo/keys.env`, 0600.
- **D7:** (b), with Exo's terms. Exo's "operator" is the floor of rules a principal cannot lower (P11), not
  a person, and a peer on the bus "is input, never consent" (D-026). So the person who helps Shahid is a
  **helper**: an approver for named categories through `policies/approvals.yaml` (`role:helper` mapped to
  that person's signed seat, D-033), never a second principal. What a helper may read is still A's call.

## OS-9. Licence and visibility (rules on proposed D8)

**Decision.** "Exo OS", MIT like Exo. The repository stays private until phase 6.

## OS-10. Exo pinned by commit

**Decision.** `exo.pin` holds the Exo commit (Exo has no tags). `tools/dev-env.sh` and
`tools/build-image.sh` check out exactly that commit. The plugin is tested inside Exo's own engine with
Exo's gate loaded through Exo's own discovery (`tests/test_plugin.py`), and those tests run before the pin
moves.

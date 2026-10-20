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

**Decision.** One root daemon, `exo-sysd`, socket-activated on `/run/exo-sysd.sock` (group `exo-sys`, mode
0660). It does five things: `status`, `snapshot.create`, `snapshot.list`, `package.install` and
`snapshot.undo` (snapper undochange for one recorded pre/post pair). Arguments are
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
- **D6:** (c) as the target. The `exo-egress` credential broker (OS-16) now injects named systemd
  credentials into HTTPS actions, so tools never hold secrets. Exo's own provider API keys for model
  calls still live in `~/.config/exo/keys.env`, 0600, read by exod (BANKAI.md §4 row S-7, proposal P-4).
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

## OS-11. Authenticate and preserve the outcome of every root action

**Decision.** Authenticate the helper with Linux kernel peer credentials and verify each receipt with
a separately published root-owned public key. Bind it to a fresh request nonce, caller, exact arguments,
result and execution state. Save mutation receipts durably before appending to the principal ledger.
If recording fails after execution, report a completed, nonretryable outcome. `exo-os system reconcile`
repairs the ledger copy without repeating the action. Details: `docs/SYSTEM-TRUST.md`.

**Why.** A signature checked only against a key supplied by the same reply proves no independent
identity. An execution failure and an audit failure require different responses. Repeating an operation
to repair its log can damage the very state the log is meant to explain.

## OS-12. Dismissing is not deciding; uncertain answers are reconciled

**Decision.** Escape defers an approval. Only explicit Yes/No answers send decisions. After an uncertain
reply or reconnection, read pending approvals before making a card answerable again. Never resend
consent automatically. Probe daemon responsiveness and event progress with bounded calls; retain
backoff across short failed connections. Details and protocol limitations: `docs/SURFACE-RECOVERY.md`.

Desktop keyboard review uses the dedicated F6 shortcut or a targeted review
button click. The review button stays outside the launcher's Tab completion
order, so completion input cannot become a review gesture.

## OS-13. Acceptance requires current, bounded evidence

**Decision.** VM acceptance first checks the unique identity of the VM started by that run. Failed
commands, malformed verification results, unavailable prerequisites and missing checks fail the run.
Host tests use isolated resources; Linux kernel/GTK checks run in a disposable container without host
mounts. An earlier image's acceptance report never counts as a pass for a new image.

**Why.** The principal's trust is a runtime property. Passing output from the wrong machine, a silent
skip, or an older build does not establish it. The core remains a tested commit dependency while its
development proceeds independently (`docs/CORE-CONTRACT.md`).

## OS-14. Boot recovery is a manual administrator operation

**Decision.** The unsigned systemd-boot image offers explicit root-admin checkpoints
and recovery staging through `exo-os-recovery`. A checkpoint preserves a read-only
system snapshot together with its matching UKI. Staging creates a separate writable
clone and a boot-menu entry. The existing `@home`, `@store` and persistent helper `@sysd` remain mounted;
original `@` is not promoted or replaced. No named root action or agent capability
is added, and the command does not select a default or reboot. Details:
`docs/BOOT-RECOVERY.md`.

**Evidence update, 2026-10-13.** The older `5a86e3b` image with core `cff62d1`
passed ten recovery checks, including an actual older-root reboot with current
persistent state retained. The built `c2d1ad9` image has not yet repeated recovery
acceptance. Neither result establishes hardware, soak or power-loss durability.
Exact identities and retained attempts: `docs/VALIDATION.md`.

**Why.** OS-4 excludes bootloader and reboot operations from the model's root door.
The EFI partition lies outside the system snapshot, so booting an arbitrary snapper
snapshot with the current kernel can mismatch its modules. Explicit checkpoints
retain the boot artifacts at the same time. EFI invocation options select the clone
only when Secure Boot is disabled for this image's unsigned UKIs; enabled or unknown
state is rejected. A signed recovery design remains separate work.

## OS-15. Every tool crosses an installed execution boundary

**Decision.** The image requires the pinned core's `exo.executors` interface and
the root-controlled OS adapter. File, search, patch and shell operations run in
a workspace-only namespace with Landlock and seccomp. Reviewed structured
desktop operations run in trusted host code; every other adapter is denied
until reviewed. Missing enforcement never falls back to a host handler. Policy,
runtime and required plugins are controlled by the image administrator.

**Why.** Isolating only the shell leaves file handlers, workers and indirect
subprocesses with host authority. The boundary belongs in shared core dispatch;
Linux enforcement belongs in this repository. Core handoff/completion also
avoid host Git inspection of tool-writable repositories. The actual kernel
tests include network/socket denial, hidden credentials and detached-child
cleanup. The remaining trust in installed code and desktop applications is
explicit in `docs/TOOL-ISOLATION.md`.

## OS-17. Ledger authority is protected state, provisioned explicitly

The desktop uses a pinned append authority over an authenticated local socket.
Each stream binds exact writer keys/attributions and a projection path in a
root-owned manifest. Keys and FULL-synchronous journal belong to static
`exo-ledger` on persistent `@signer`, outside system rollback; public identity
stays root-owned. Missing established state refuses rather than reseeding.
Fresh root provisioning precedes initializer/runtime writes, and refuses local
history or partial state. The built image selects required authority mode; booted
image acceptance remains a separate gate. Checkpoint export includes genesis and is ready for
an independently pinned witness; no remote endpoint or automatic delivery is
configured. See `docs/LEDGER-AUTHORITY.md`.

Every provisioned writer permits the desktop account `exo` as its caller.
Separate writer keys do not isolate processes within that UID (Unix user ID).
Writer attribution therefore trusts that desktop UID, except for appends as the
`principal` writer, which OS-21 binds to the core process. The service still
checks the caller UID for each stream/writer and enforces each writer's allowed
authors. Signer keys and journal ownership remain separate from desktop processes.

Trusted providers, plugins and desktop applications under `exo` can request
signatures for any other configured writer. Sandboxed tools have no authority
socket in their box by design, with current native proof pending. The other
writers, `exo-gate` and those that may attribute an event to the principal among
them, are not bound to the core: proposed OS-22 in `DECISIONS-PROPOSED.md`.

## OS-16. Credentialed actions preserve both consent and uncertainty

**Decision.** A separate unprivileged `exo-egress` service injects named systemd
credentials into HTTPS requests permitted by root-owned profiles. The exact
expected origin, method, path, body and operation ID are bound to one-shot
approval and shown in full. The service checks the expected origin against its
loaded policy, never treats it as routing authority, and returns only bounded
outcome metadata. No destination or credential is granted in the shipped image.

Before sending, the service durably reserves each ID and argument fingerprint.
An uncertain operation is never sent again; its journal persists on `@egress`
across system recovery. Missing enforcement, unavailable identity, changed
arguments or unverifiable approval details refuse the action. Root's activated
listener and the service sender are authenticated separately with Linux kernel
credentials. Details: `docs/CREDENTIAL-EGRESS.md`.

**Why.** A lost reply does not establish that an external action failed, and
rolling back the OS must not erase that uncertainty. A destination label alone
does not reveal what the person is approving. Arbitrary upstream response text
can also reflect an injected credential in an encoding a generic redactor
misses, so read APIs need reviewed response adapters before being enabled.

## OS-18. Witness delivery is explicit and optional

**Decision.** The image stages an administrator-only witness publisher, disabled
until explicit offline enrollment. A no-network `exo-ledger` exporter emits only
verified checkpoints; a separate `exo-witness` process sends the bounded prefix.
The root parent verifies a fresh nonce-bound witness head and durably retains the
prior verified receipt before publishing new evidence. Details:
`docs/WITNESS-PUBLISHER.md`.

**Why.** A signed local checkpoint needs an independently retained receipt to
support outside rollback evidence. This implementation does not establish that
the witness is independently administered or remotely deployed; Linux/image
acceptance and an actual independent endpoint remain required.

## OS-19. The root helper serializes mutations, not requests

**Decision.** `exo-sysd` runs mutating actions one at a time under a mutation lock and
holds a separate log lock only to open, append to and close the audit log. Read-only
actions (`READ_ONLY` in `sysd/protocol.py`: `status`, `snapshot.list`) take no
mutation lock. Any action not listed as read-only is a mutation. The mutation lock
is held through the log append, so audit rows appear in execution order, and the log
is still opened before a mutation runs. Details: `docs/SYSTEM-TRUST.md`.

**Why.** One lock held for a whole action let a long install block every other
request, including status. The guarantees that mattered were that mutations never
overlap and that a known audit outage never lets one run; both remain. Rejected:
a lock per action, which would let two mutations touch the package database and
snapshots at once. Known limit: a request waiting for the mutation lock still holds
one of the eight connection slots.

## OS-21. The principal writer signs only for the core process its launch record names

**Decision.** The ledger signer appends as the `principal` writer only for the
process that the core's launch record (Exo D-060) names. The record is private
to the desktop user, so the signer cannot open it. The exo-os client opens it and
sends the open file with the append (`SCM_RIGHTS`). The signer accepts it only
as a private single-link regular file, owned by the caller UID, on the store
filesystem, that the kernel names at `$EXO_HOME/run/exod.principal.key`. The
record must name the connection's kernel peer: the same pid, the caller UID and
the process start identity read at accept, and that pid must still be the same
process. Any doubt refuses before the journal is touched, so the reply is a known
refusal, never an uncertain append. Reads, and every other writer, keep the
caller-UID rule of OS-17. The JSON protocol, manifest, journal and signatures are
unchanged. Details: `docs/LEDGER-AUTHORITY.md`.

**Why.** Issue #106: every writer accepted any process running as `exo`, so a
tool process that reached the socket could sign as `principal`. D-060 already
names one process per launch, by pid and a start identity that a reused pid does
not share. Binding to it closes the principal writer to every other process,
including one that holds a copy of the record. No core change is needed, and
nothing in the image appends as `principal`: exod writes as `exo-gate`,
`exo-memory`, `exo-learn` and `exo-os`, and the OS commands as `exo-os`.

**Rejected.** Letting the signer read the record: an ACL shows in the record's
group bits, which the core's own reader refuses, and `CAP_DAC_READ_SEARCH` would
let the signer read every desktop file. A record sent as data: any process can
write one that names itself. Exod's cgroup or parent: any process of the user can
join that cgroup or be started by the user manager. A root service that copies the
record for the signer: a new root component for the same strength, since the
record's folder is the user's.

**Limits.** Same-user code outside the tool box can rewrite the record to name
itself, as D-060 says. That also stops the real core's principal clients until
the core restarts. A pid reused between connect and accept is not detected. The
check needs the core's `/proc` entries readable by the signer. Command-line
requests for the `principal` writer (`exo log append --author principal`) now
refuse on the image. The principal-name writer, `exo-memory`, `exo-learn` and
`ingest` can still attribute events to the principal from any `exo` process, and
`exo-gate` is unbound: proposed OS-22. Native proof on a booted image is pending:
an exod append through the signer's mount namespace, and a refusal for another
`exo` process.

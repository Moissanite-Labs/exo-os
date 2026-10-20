# Model spend on Exo OS

Exo OS starts with a zero paid-model budget. At first boot, after the protected
ledger identity exists and before login, `exo-model-spend-provision.service`
creates one spend journal on the persistent `@store` subvolume. It publishes the
root-controlled policy last. The initial policy has principal `exo`, daily and
monthly USD ceilings of `"0"`, no priced routes, and no paid destination or key.
The exact installed `RuleFake` remains available offline. Every routed paid
provider is refused before HTTP until an operator explicitly installs a priced
route and caps in the root policy.
Known unpriced or invalid requests refuse only that request. They leave the
offline model and journal usable. A call that may have incurred an uncertain
charge stops later admission until the principal reviews the journal.

| Path | Owner and mode | Purpose |
|---|---|---|
| `/var/lib/exo/model-spend` | `root:root 0755` | Persistent policy parent |
| `/var/lib/exo/model-spend/policy.json` | `root:root 0644` | Fixed principal, journal UUID, caps and route contracts |
| `/var/lib/exo/model-spend/exo` | `exo:exo 0700` | Private journal parent |
| `/var/lib/exo/model-spend/exo/journal.sqlite` | `exo:exo 0600` | Reservations, charges and stop state |
| `/var/lib/exo/model-spend-repair` | `root:root 0700` | Refused state preserved by [root repair](#root-repair), only after it runs |

`EXO_MODEL_SPEND_REQUIRED=1` and
`EXO_MODEL_SPEND_POLICY=/var/lib/exo/model-spend/policy.json` are installed in
the global environment, `exod.service` and `exo-os-setup.service`. The core
loads that exact root-controlled file; mutable principal or seat configuration
cannot replace its caps or routes. `exo-model-spend-admin status` is a root-only,
read-only inspection command. `models.list` and the surface's `budget`, `spending`
or `model budget` palette query show the principal's state and ceilings without
starting a model turn or opening an approval card.

On every later boot the service checks the existing policy, fixed principal and
journal path, journal UUID, exact owners and modes, and the ledger/store mount
identity. Missing, partial, symlinked, misowned or inconsistent state refuses
startup, except a [first boot cut off before publication](#first-boot-cut-off-before-publication).
The image installs no documentation, so each refusal ends with a short hint. For
state after publication it says the state is never reset automatically. For other
state it names [root repair](#root-repair) and its limit. A crash after atomic
policy publication can leave one `.policy-` file
with exactly 16 lowercase hexadecimal characters. Boot activation removes it
only after validating the published policy and original journal, and only if
it is a root-owned regular `0644` file with the policy's exact device/inode and
both have link count two. Status may inspect this verified state without removing
the link. Unrelated entries or other hard links still refuse. This cleanup does
not change policy bytes, ceilings or the journal UUID. It does not recreate the
journal or restore a default policy. Repair of state after publication requires a
deliberate administrator procedure with the daemon stopped and preserved evidence.
Neither boot nor root repair resets it. This repository does not expose cap
editing, journal reset, or resume through an agent tool.

An operator who deliberately enables a paid route must pin the native
transport, endpoint, model, input/output bounds and all four token prices in
the protected policy. Daily and monthly caps should be conservative, with
room for every possible reservation. Review pending reservations and the
provider bill before any journal reconciliation. A stopped journal remains
readable for status and transcript access; generation and tool execution stop.
The OS acceptance suite exercises the offline path, a known unpriced refusal,
and a priced native route rejected by the zero cap before mocked HTTP. The
priced check uses a temporary root-owned policy under `/run` with the same
journal identity and leaves the persistent policy byte-for-byte unchanged.
Another offline turn verifies the known refusals did not freeze the journal.
A separate synthetic uncertainty then checks stopped status across a daemon
restart. The persistence check re-runs `exo-model-spend-admin ensure-boot` as
root without restarting its boot-order service, then restarts only `exod` and
checks the journal UUID and policy hash. The suite makes no paid call and gives
no evidence of real provider billing accuracy.

This boundary covers Exo's routed model calls in the installed trusted core.
Installed providers and plugins, plus other processes under the `exo` UID, are
trusted. A plugin or same-UID process can edit the user-owned journal, and an
operator can change the root policy. The journal is local accounting, not an
account-wide provider billing firewall. `@store` persists across an OS root
rollback, but this design cannot detect a coherent rollback of the entire
persistent store and its journal. Independent billing evidence or a remote
witness would be needed for that claim.

Verification before enabling paid use: run
`EXO_OS_VM_ACCEPTANCE=1 tools/accept-model-spend.sh --vm-acceptance --image-source <full-built-OS-commit>`
against a disposable `vmtest` image. The clean committed harness revision is
recorded separately from the named image revision. For a tools-only harness
correction, the existing image can be reused only if the installed and local
build manifests, pinned core, runtime/image digest and throwaway VM identity
still match; the corrected tools do not replace installed runtime files. Run
`tools/accept-isolation.sh` for
tool denial of both policy and journal. Recovery persistence is checked by
`tools/accept-recovery.sh`. The VM acceptance script changes only its
disposable overlay and leaves the test journal stopped by its synthetic fixture.

The older `5a86e3b` image with core `cff62d1` passed twelve spending checks using
the corrected tools-only harness `e26775f`. Its recovery checks preserved the
post-checkpoint stopped journal through an actual older-root reboot. These are
offline/mocked-HTTP results. Paid usage, billing accuracy and the new `c2d1ad9`
image's spending acceptance remain unverified. The original failed attempt is
retained. See [historical evidence](VALIDATION.md#historical-image-5a86e3b).

## First boot cut off before publication

A power cut between creating `/var/lib/exo/model-spend` and linking `policy.json`
leaves a journal without a policy. Login waits for this unit, and the core opens the
journal only through `policy.json`. Boot cannot tell whether a policy ever named the
journal, because a policy deleted before `.owned` exists leaves the same files. So it
finishes provisioning only when the files show the journal was never used:

- `/var/lib/exo/.owned` is absent, so first boot never finished. The store holds
  only `model-spend` and the empty root-owned `exo` skeleton.
- `policy.json` is absent. Every directory and file, with its owner and mode, is
  one that first boot creates at some step under the unit's `UMask=0077`.
- A journal, if present, opens under the `exo` UID and passes SQLite's quick check.
  It has only the `identity` and `calls` tables, no call row, no stop reason and an
  observed clock of zero. A hot rollback journal is rolled back first.
- At most one root-owned `.policy-` temporary file with a single link exists, and
  only beside a journal with an identity. Nothing reads it, so boot removes it.

When the journal has an identity, boot publishes the zero-cap policy for that same
journal UUID through the usual link. When no identity row was ever committed, boot
removes the directories and provisions from the start. It removes the rollback
journal before its database, so a cut during removal leaves state the next boot
accepts again. Both end with the default policy: zero caps and no routes. Any other
state still refuses. Examples are a journal with one call, a stop or a moved clock,
an extra entry, or a store whose ownership transfer already finished.

## Root repair

`exo-model-spend-admin repair` runs as root from the console while login is
blocked. It needs a valid ledger, so run `exo-ledger-admin repair` first if the
ledger unit also failed. The command first runs the normal boot check, which may
finish a cut-off first boot. If that check passes, it stops there and prints the
policy. A stopped journal passes, so repair never clears a stop on valid state.

Otherwise it acts only when the refused state shows the same lack of use as a
cut-off first boot. `.owned` and `policy.json` are absent. The store holds only
`model-spend`, the empty `exo` skeleton and any earlier repair directory. Every
entry is a kind first boot writes. A journal, if present, has no call, no stop and
an observed clock of zero under the `exo` UID. A temporary policy needs a journal
with a committed identity beside it. Owners, modes, link counts and the number of
temporaries may differ from every first-boot step. That is why boot refused, since
boot deletes or publishes from what it accepts.

It then renames the `model-spend` entry into a new
`/var/lib/exo/model-spend-repair/<UTC time>-<random>/` directory. That directory is
root-only, mode 0700, on the same subvolume. Nothing is deleted. The command then
provisions a new journal with the zero-cap policy and prints JSON with the preserved
path, the refusal and the new policy. Reboot afterwards. A cut during repair leaves
`/var/lib/exo/model-spend-repair` in the store, which boot does not accept. Run
repair again.

Any other state refuses unchanged. That includes any `policy.json`, a journal with a
call, a stop or a moved clock, a temporary policy without its journal, a store whose
first boot finished, and any entry repair cannot classify. Repairing that state stays a deliberate administrator
procedure with the daemon stopped and preserved evidence.

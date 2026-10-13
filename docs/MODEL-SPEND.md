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
startup. A crash after atomic policy publication can leave one `.policy-` file
with exactly 16 lowercase hexadecimal characters. Boot activation removes it
only after validating the published policy and original journal, and only if
it is a root-owned regular `0644` file with the policy's exact device/inode and
both have link count two. Status may inspect this verified state without removing
the link. Unrelated entries, other hard links, or a crash before publication
still require explicit repair. This cleanup does not change policy bytes,
ceilings or the journal UUID. It does not recreate the journal or restore a
default policy. Repair requires a deliberate administrator procedure with the daemon stopped and
preserved evidence. This repository does not expose cap editing, journal reset,
or resume through an agent tool.

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

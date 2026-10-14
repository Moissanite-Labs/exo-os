# Protected ledger authority

The image configuration activates the protected append authority before the first login.
The root boot service validates persistent mount identities on every boot. It
provisions a new stream only when both the signer mount and root-owned store
skeleton are empty. Existing identity and journal must validate under the signer
UID; incomplete state stops login and needs explicit administrator repair. It
does not migrate an already running machine. The core dependency is pinned in
`exo.pin`; its contract is `docs/LEDGER-AUTHORITY.md` in the Exo repository.

## Ownership and persistence

Persistent `@signer` mounts at `/var/lib/exo-ledger`, outside system rollback.
That mount and `manifest.json` are root-owned, respectively 0755 and 0644. The
manifest independently pins each opaque stream's public checkpoint key, separate
writer keys, permitted author attributions, exact local projection path and
static caller accounts. The service cannot change or replace it.

`private/` and each stream directory belong to static `exo-ledger`, mode 0700.
Journal and seed files are 0600. They never enter desktop processes, workspace
workers, network requests or ordinary wire-signing keys. Opening an established
stream requires the existing journal, every key and the matching root identity.
Missing, corrupt or mismatched state refuses startup. No daemon path seeds or
imports history. Firstboot's home/store chown excludes this mount.

Recovery preflight and captured fstab require `@signer` on the same btrfs
filesystem, alongside home, store, sysd, egress and snapshot mounts. Native
system snapshots exclude its contents. Changing boot roots therefore does not
rewind signer state. This protects ordinary rollback, not an administrator who
can replace protected state and its public identity together.

## First boot and established state

`exo-ledger-provision.service` runs as root before firstboot, greetd,
systemd-user-sessions, and the authority socket. It requires `/var/lib/exo`
and `/var/lib/exo-ledger` to be exact `@store` and `@signer` Btrfs mounts on
the same filesystem. Fresh provisioning opens the journal only after a child
drops to `exo-ledger`. On later boots it verifies the existing manifest and
journal under that UID; a missing or mismatched component fails boot gating.

Provisioning refuses an existing root manifest, any existing local ledger at the
bound projection path or any partial private signer state. An interrupted
provision requires explicit administrator repair. Established local signed or
unsigned history needs a separately reviewed migration; it is never auto-imported
or replaced by a fresh genesis.

The shipped single-user inventory binds `/var/lib/exo/exo/principals/exo/ledger.sqlite`.
Each writer uses its own event key: `exo-gate`, `exo-memory`, `exo-learn`, `exo-os`,
`exo-onboard`, `exo-consolidate`, `ingest`, `agent`, `principal`, and the configured
principal name. Memory defaults to agent attribution `exo-memory`; memory and
learning permit the configured principal and literal `principal` attribution.
Ingest permits its own attribution, these principal attributions and the fixed
import labels `chatgpt`, `claude.ai`, `claude-code` and `grok`. Custom memory agents,
Hermes profiles, seats and extra authors are intentionally unprovisioned until an
administrator reviews a migration. There are no wildcard authors or paths.

## Caller trust boundary

Provisioning sets every writer's caller list to `["exo"]`. The service checks
the kernel UID (Unix user ID) against the selected stream/writer's caller list,
and the journal enforces that writer's allowed author attributions. Distinct
writer keys do not identify or isolate individual processes within the desktop
UID. Writer attribution is only as strong as trust in that UID.

Trusted providers, plugins and desktop applications running as `exo` can request
signatures for any configured writer, subject to its allowed authors and append
rules. They cannot read the signer's private keys or journal through these
permissions. Those files remain owned by the separate `exo-ledger` account.

The tool sandbox creates an empty `/run` and does not expose the authority
socket. This is the configured box boundary, with current native proof pending.
Planned principal credentials and authenticated binding to a specific core
process are not implemented. The current protocol authenticates a caller UID,
not a particular provider, plugin, desktop application or human principal.

## Runtime binding

The image ships the required core/user-unit environment:

```text
EXO_LEDGER_AUTHORITY=exo-os
EXO_LEDGER_AUTHORITY_REQUIRED=1
```

`exo-os-setup` verifies the bound authority head before invoking `exo init`,
then writes configuration and the machine profile. An absent binding or signer
stops setup before initializer writes. The OS ledger sink uses the shared core
factory before any local key read or generation. A selected/required authority
with no store refuses; ordinary mode retains its local sink behavior.

## Local transport and uncertain outcomes

Systemd creates `/run/exo-ledger.sock` as root:exo-sys, mode 0660. The client
verifies root-controlled ancestors, socket owner/mode/group, the listener's
`SO_PEERCRED`, and the actual signer's `SCM_CREDENTIALS` greeting before sending
an intent. A root-created inherited listener alone does not prove the worker UID.
Unexpected ancillary data and received descriptors are rejected and closed.
The service authenticates the caller's kernel UID before reading payload, then
checks the stream/writer against that account's exact manifest permissions.

One strict finite JSON document, at most 1 MiB, travels per connection. Duplicate
keys, unknown fields, caller-selected filesystem/key paths and excessive replay
batches are refused. Reads/writes have deadlines; four workers bound concurrency.
The service has no network address family, credential requests or remote witness
endpoint. Its private state is its only writable system path.

Appends carry one stable operation ID and expected head. They commit the signed
event, checkpoint and lookup index in one FULL-synchronous journal transaction.
An authenticated head conflict means synchronize first. Lost, invalid or uncertain
append replies retain that operation ID: look it up, never repeat the external
action or substitute a new ID. A verified committed append whose desktop copy
fails is projection recovery, not permission to append again. The desktop ledger
is a verifiable local projection, not the authority or signing-key owner.

`exo log verify` and `exo memory verify` use the configured authority in read-only
mode. A missing tail, altered row, extra local row or unavailable authority cannot
be reported as a verified copy. Verification preserves the rows and triggers it
inspects. A changing authority head is retried briefly; a persistently busy stream
reports unverifiable. A missing tail can also mean an interrupted projection write,
so that result alone does not establish malicious deletion. Open a normal writer
to synchronize committed rows explicitly; a divergent local prefix refuses repair.

`tools/accept-ledger.sh` exercises the actual installed service, both verification
commands, deliberate edits/truncation, private-key access denial and signer outage
in a fresh disposable VM. `tools/accept-phase1.sh` exports a coherent SQLite backup
and checks it in plain core on the host, using public identity obtained separately
from the protected root manifest. Neither check constitutes a deployed witness.

## Independent witness handoff

`exo-ledger-admin export-checkpoints` emits genesis even for an empty stream,
then at most 64 checkpoint-only records per stream up to a captured head. It does
not read private event bodies merely to discard them. Continue with
`--since NEXT_SEQUENCE --limit 64`; export does not transmit, acknowledge or
claim an independent witness exists.

An operator transfers the root-pinned public stream `identity` from
`/var/lib/exo-ledger/manifest.json` and the exported signed genesis through an
independently authenticated route. The separate core witness draft pins those inputs explicitly
with `WitnessJournal.provision`; that API is outside the current OS pin. A witness must never adopt identity from an
append RPC reply. Later checkpoint exports are verified against that pin and
previous checkpoint. Witness endpoints, delivery/acknowledgment and live
off-machine acceptance remain unconfigured.

For the core verifier's raw checkpoint JSONL input, use
`exo-ledger-admin export-checkpoints --stream STREAM_ID --raw`; this requires one
explicit stream and includes its signed genesis by default. The default wrapped
format also names the stream but must be unwrapped before that verifier.

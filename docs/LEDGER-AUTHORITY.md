# Protected ledger authority

The image configuration activates the protected append authority before the first login.
The root boot service validates persistent mount identities on every boot. It
provisions a new stream only when both the signer mount and root-owned store
skeleton are empty, or when state left without a manifest provably signed nothing.
Existing identity and journal must validate under the signer UID. Other incomplete
state stops login. [Root repair](#root-repair) acts only on state that shows nothing
was signed or published. It does not migrate an already running machine. The core
dependency is pinned in `exo.pin`; its contract is `docs/LEDGER-AUTHORITY.md` in the
Exo repository.

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
bound projection path, any private signer state and any leftover temporary
manifest. It writes the manifest to a temporary file, syncs it and the private
state, then links it into place. A power cut therefore leaves either no
`manifest.json` or a complete one, never a truncated file. A cut right after the
link can leave the temporary hard link. Boot removes it only after validating the
manifest and journal, and only when it is a root-owned 0644 file with the
manifest's device and inode and both have two links. Established local signed or
unsigned history needs a separately reviewed migration. It is never imported or
replaced by a fresh genesis, at boot or by root repair. This is the OS-17 rule in
`DECISIONS.md`, and it is unchanged.

The shipped single-user inventory binds `/var/lib/exo/exo/principals/exo/ledger.sqlite`.
Each writer uses its own event key: `exo-gate`, `exo-memory`, `exo-learn`, `exo-os`,
`exo-onboard`, `exo-consolidate`, `ingest`, `agent`, `principal`, and the configured
principal name. A separate key does not separate callers. Only the `principal`
writer is bound to one process, the core ([caller trust boundary](#caller-trust-boundary)).
Memory defaults to agent attribution `exo-memory`; memory and
learning permit the configured principal and literal `principal` attribution.
Ingest permits its own attribution, these principal attributions and the fixed
import labels `chatgpt`, `claude.ai`, `claude-code` and `grok`. Custom memory agents,
Hermes profiles, seats and extra authors are intentionally unprovisioned until an
administrator reviews a migration. There are no wildcard authors or paths.

### Provision cut off before publication

Without a manifest the signer service cannot start. Boot cannot tell a provision
cut off before its manifest from a manifest removed before anything was signed, so
it relies on what it can check. The next boot clears what was left and provisions
again only when all of these hold:

- `manifest.json` is absent. The store is still the empty root-owned `exo`
  skeleton with nothing else, so no projection exists.
- The signer mount holds only `private/` and at most one temporary
  `.manifest-` file with a single link, which nothing reads. `private/` holds at
  most one `s_` stream directory. It contains only the key files, journal and
  SQLite side files that provisioning writes, with the owners and modes it sets at
  each step.
- The journal, if present, opens under the `exo-ledger` UID and passes SQLite's
  quick check. It has only the core's tables and holds no event, no operation and
  no checkpoint above genesis.

Boot then removes exactly those entries through held directory descriptors and
provisions a new stream. It removes them in the reverse of provisioning order: the
temporary manifest, then each stream's SQLite side files, journal and keys, then
the directories. A cut during removal therefore leaves state the next boot accepts
again. The keys never left the signer mount and nothing was signed, so nothing is
lost. Everything else refuses. Examples are a manifest that fails validation, a
projection, any signed event, any head above genesis, or an entry provisioning
does not write. The image installs no documentation, so each refusal ends with a
short hint. Without a manifest it names `exo-ledger-admin repair` and its limit.
With one, it says a published ledger is never reseeded.

### Root repair

`exo-ledger-admin repair` runs as root from the console while login is blocked. It
first runs the normal boot check, which may clear a cut-off provision. If that
check passes, it stops there and prints the manifest. Otherwise it acts only when
the refused state shows that nothing was signed or published:

- `manifest.json` is absent. The store is still the empty root-owned `exo`
  skeleton, so no projection exists.
- Every signer mount entry is a kind provisioning writes: `private/`, `s_` stream
  directories, their key files, journal and SQLite side files, and `.manifest-`
  temporaries beside at least one journal. SQLite side files never stand without
  their journal, because a WAL file alone may hold signed frames. Owners, modes,
  link counts and the number of streams or temporaries may differ from every
  provisioning step. That is why boot refused, since boot deletes what it clears.
  An earlier `repair/` directory must be root-owned with no group or other access.
- Every journal opens under the `exo-ledger` UID, passes SQLite's quick check and
  holds no event, no operation and no checkpoint above genesis. So a root-owned
  `private/` or stream directory that this UID cannot enter refuses when it holds a
  journal.

It then renames every signer mount entry into a new
`/var/lib/exo-ledger/repair/<UTC time>-<random>/` directory, root-only and mode
0700. Nothing is deleted, and the old keys are out of the signer's reach. It
provisions a new stream and prints JSON with the preserved paths, the refusal and
the new manifest. Reboot afterwards. Run `exo-model-spend-admin repair` only if the
spend unit still refuses. A cut during repair leaves its `repair/` directory, which
boot does not accept. Run repair again.

Any other state refuses unchanged. That includes a manifest that fails validation,
a projection or other store content, any signed event, any head above genesis and
any entry repair cannot classify. A new genesis never replaces established
history, at boot or by repair. Such state needs a separately reviewed migration.

## Caller trust boundary

Provisioning sets every writer's caller list to `["exo"]`. The service checks
the kernel UID (Unix user ID) against the selected stream/writer's caller list,
and the journal enforces that writer's allowed author attributions. Distinct
writer keys do not identify or isolate individual processes within the desktop
UID. For every writer except `principal`, writer attribution is only as strong
as trust in that UID.

### The principal writer and the core's launch record

An append as the `principal` writer is signed only for the core process that
its launch record names (OS-20, issue #106). The core (`exo serve`) writes that
record before its socket accepts, beside the socket:
`/var/lib/exo/exo/run/exod.principal.key`, owner-only in an owner-only folder
(Exo D-060). It binds the core's pid, user ID and process start identity for
that launch.

The signer runs as `exo-ledger` and cannot open that file. The exo-os client
opens it and sends the open file with the append, as `SCM_RIGHTS` on the
request's first bytes. The JSON request is unchanged. Before it touches the
journal, the signer checks all of these:

- One descriptor came, and no other ancillary data.
- It is a regular file with one link, owned by the caller UID, with no group or
  other access, at most 4 KiB, on the same filesystem as the core's home.
- The kernel names it at the record's exact path. A copy, or a file in another
  mount namespace, does not count. An older record that a new launch replaced
  has no link left.
- It is a v1 record that names the connection's kernel peer: the same pid, the
  caller UID, and the process start identity the signer read at accept.
- That pid is still the same process.

Any failure is a known refusal (`refused`), never an uncertain append, so the
caller has nothing to look up. An append from any other process is refused,
including one that holds the real record. Reads of the stream (`head`,
`read_since`, `lookup`) keep the caller-UID rule, so any `exo` process can still
verify and synchronize a projection that holds principal events. A descriptor on
any other request refuses.

In the shipped image nothing else appends as `principal`: exod's own writers are
`exo-gate`, `exo-memory` and `exo-learn`. Command-line requests for the
`principal` writer, such as `exo log append --author principal` or
`exo ask answer --author principal`, now refuse on the image.

What the binding does not cover:

- Same-user code outside the tool box can rewrite the record to name itself, as
  D-060 says. Doing so also stops the real core's principal clients until the
  core restarts.
- The configured principal's writer (`exo`, whose author is the store id), and
  `exo-memory`, `exo-learn` and `ingest`, which may attribute an event to the
  principal, still trust the desktop UID. So does `exo-gate`. Proposed OS-21 in
  `DECISIONS-PROPOSED.md` sets out the choices.
- A pid reused between connect and accept is not detected (D-060's limit).
- The check reads `/proc` entries of the core's processes. `hidepid` or
  `ProtectProc` for the signer would make it refuse every principal append.
- Native proof on a booted image is pending: an exod append through the
  signer's own mount namespace, and a refusal for another `exo` process. The
  host suites run the real signer `Server` on a real Unix socket with a real
  launch record written by the core's own code
  (`tests/test_ledger_principal_core.py`).

### Other writers

Trusted providers, plugins and desktop applications running as `exo` can request
signatures for any other configured writer, subject to its allowed authors and
append rules. They cannot read the signer's private keys or journal through
these permissions. Those files remain owned by the separate `exo-ledger` account.

The tool sandbox creates an empty `/run` and does not expose the authority
socket. This is the configured box boundary, with current native proof pending.
The protocol authenticates a caller UID, and for the `principal` writer the core
process. It does not identify a particular provider, plugin, desktop application
or human principal.

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
checks the stream/writer against that account's exact manifest permissions. A
`principal` append must also carry the core's launch record as a descriptor
([above](#the-principal-writer-and-the-cores-launch-record)). The service closes
every descriptor it receives and refuses more than one.

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

The older `5a86e3b` image with core `cff62d1` passed twelve ledger checks and
preserved post-checkpoint signer identity/history through an actual older-root
reboot. Phase 1 verified a portable signed prefix through sequence 7. Phase 2
verified sequence 49. These are historical results. Ledger acceptance on the
built `c2d1ad9` image, independent witness deployment and four witness kernel cases and one installed-flow case remain pending. See
[VALIDATION.md](VALIDATION.md#historical-image-5a86e3b).

## Independent witness handoff

`exo-ledger-admin export-checkpoints` emits genesis even for an empty stream,
then at most 64 checkpoint-only records per stream up to a captured head. It does
not read private event bodies merely to discard them. Continue with
`--since NEXT_SEQUENCE --limit 64`; export does not transmit, acknowledge or
claim an independent witness exists.

An operator transfers the root-pinned public stream `identity` from
`/var/lib/exo-ledger/manifest.json` and the exported signed genesis through an
independently authenticated route. The pinned core witness draft binds those inputs
explicitly with `WitnessJournal.provision`. A witness must never adopt identity from
an append RPC reply. Later checkpoint exports are verified against that pin and
previous checkpoint. Optional [administrator witness delivery](WITNESS-PUBLISHER.md)
captures and verifies a complete bounded prefix before transmission, and keeps cached
receipts separate from challenged fresh status. Enrollment is disabled by default;
independent endpoints and live off-machine acceptance remain unconfigured.

For the core verifier's raw checkpoint JSONL input, use
`exo-ledger-admin export-checkpoints --stream STREAM_ID --raw`; this requires one
explicit stream and includes its signed genesis by default. The default wrapped
format also names the stream but must be unwrapped before that verifier.

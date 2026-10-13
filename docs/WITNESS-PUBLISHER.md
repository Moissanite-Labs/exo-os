# Optional administrator witness delivery

`exo-witness-admin` exports a protected authority's checkpoint history to one
explicitly enrolled witness. It is disabled by default. This branch pins core
`b060974a514cf08c0765119e2e8d952682102a0c`, including the draft witness transport
and its public challenged-head API. It does not activate a witness in an existing
image, deploy a remote service, or establish independent administration.

There are no timers, services, boot/login dependencies, generated credentials,
discovery, or model-callable configuration actions. The image installs a static
`exo-witness` account and the command. It creates no witness state: fresh `@store`
still contains exactly the empty principal skeleton required by ledger provisioning.

## Explicit offline enrollment

First provision the protected authority through the existing ledger boot process.
Independently arrange and authenticate a witness, its signed genesis receipt,
witness signing public key, dedicated CA, client certificate/key, server hostname,
and exact server leaf certificate SHA-256 pin. The witness signing key must differ
from the authority checkpoint and writer keys. Separate keys alone do not establish
independent ownership, storage, administration, or rollback domains.

Prepare a root-owned directory, such as `/root/witness-enrollment`, mode `0700`,
containing these root-owned regular files, each mode `0600` with one link:

| File | Contents |
| --- | --- |
| `policy.json` | Exact schema below; no extra or duplicate fields |
| `receipt.json` | Independently supplied signed witness receipt for the pinned genesis |
| `ca.pem` | Dedicated TLS CA certificate(s) |
| `client.pem` | Publisher TLS certificate chain |
| `client.key` | Matching unencrypted TLS private key; no interactive password prompt |

All ancestors must be root-owned directories without group/world write permission.
Symlinks, hard-linked files, special files and files over 64 KiB are refused. Obtain
the public authority identity and signed genesis through the protected ledger admin
export described in [LEDGER-AUTHORITY.md](LEDGER-AUTHORITY.md). Provision/enroll the
remote witness separately using the core witness tooling; this command never does
so. Do not place authority signing keys in the bundle.

The policy has exactly these fields (placeholders are not usable configuration):

```json
{
  "version": 1,
  "identity": "replace with the complete protected authority identity object",
  "genesis": "replace with the complete signed sequence-zero checkpoint object",
  "witness_pub": "base64 Ed25519 witness public key",
  "host": "192.0.2.1",
  "port": 443,
  "server_hostname": "witness.example",
  "server_cert_sha256": "replace with 64 lowercase hexadecimal digits",
  "timeout": 10
}
```

`host` must be a numeric IP, `port` an integer from 1–65535, and `timeout` a
finite number from 0.05–60 seconds. The identity and genesis must each fit the
transport's 8192-byte document limit. TLS uses the supplied CA and exact leaf pin;
ambient proxy settings and default trust stores do not select the endpoint.

As root:

```sh
exo-witness-admin status
exo-witness-admin initialize --bundle /root/witness-enrollment
exo-witness-admin status
exo-witness-admin publish
exo-witness-admin status --fresh
```

Initialization is offline. It validates the existing protected authority manifest,
genesis receipt and TLS key pair before creating state. It writes the fixed
`/var/lib/exo/witness-publisher/` directory (`0700`) with policy, credentials,
`current.json` and `previous.json` (`0600`), fsyncs them, then publishes the root-owned
`/var/lib/exo/witness-enabled.json` marker last. These paths reside on existing
persistent `@store`, outside system-image rollback. An absent directory and marker
report `disabled`, without importing witness dependencies. Either one missing alone,
any missing enabled-state file, or any corrupt/mismatched state is an error.
Initialization never overwrites or silently reseeds an existing or partial enrollment.

## Export and process boundaries

The root parent reads only protected public authority metadata, enrolled TLS
credentials and receipt evidence. It never loads authority signing keys or opens a
remote connection. Every worker is a separate exec with a fixed installed Python
path, cleared environment, closed unrelated descriptors, empty supplementary groups,
and its intended UID/GID set before Python starts. No shell is involved.

The exporter runs as `exo-ledger`. It applies no-new-privileges and a syscall filter
that denies networking **before** importing the journal/key loader. It captures one
target checkpoint, then exports only the complete genesis prefix through that target,
in bounded batches of 64. The target does not advance when the live authority grows.
No event bodies are exported. The prefix is limited to 64 MiB with individual lines
limited to 8193 bytes; larger histories refuse until a separately reviewed protocol
exists. The root parent verifies every signature/link and the exact captured target
before starting any network process.

The publisher runs as a distinct static `exo-witness` UID/GID, with no signer group,
desktop identity, or append-socket group. Its root-owned temporary `/run` snapshot
contains only the checkpoint prefix, pinned public policy, and enrolled TLS material.
The directory is `0750` and files are `0440`, group-readable by the publisher;
only its empty scratch directory is writable. Landlock limits filesystem reads to
runtime directories, `/proc`, and this snapshot, with writes only to scratch. DAC
and cleared groups deny signer keys and the authority socket. Temporary material is
removed on ordinary return or exception. A killed/crashed parent can leave its TLS
client key and prefix snapshot readable to the trusted `exo-witness` UID in `/run`
until reboot or administrator cleanup. Both workers have resource limits and
parent-death killing; that child protection does not clean up a dead parent's snapshot.

Each child has one 90-second wall deadline. Export stdout is bounded to the 64 MiB
prefix plus target header; network stdout to 16 KiB; stderr to 8 KiB. Failure kills
and reaps the child. Stderr is never echoed because it may contain credential details.
Large histories can fail the deadline even below the byte limit. No limit is raised
automatically. A witness already ahead of the captured export refuses publication.

## Durable evidence and failures

The parent independently verifies the child's publication receipt against the exact
pinned checkpoint. It also supplies a new random nonce and verifies the returned
signed head envelope itself. Only an exact matching challenged head permits local
receipt publication. If another publisher advances the witness during delivery,
this command refuses local publication rather than chasing the new head.

Evidence binds the identity/genesis hashes, witness key, exact checkpoint and receipt,
captured authority target, and descriptive local observation time. Lower receipts,
conflicting receipts at a saved sequence, and changed local witnessed prefixes refuse.
The prior verified evidence is atomically replaced into `previous.json` and fsynced
before `current.json` is atomically replaced and fsynced. Success is reported only
after both directory syncs. A final fsync failure can leave the newer current file
visible with uncertain durability; the previous verified receipt remains retained.

A network error, lost reply, process kill or disk failure may occur after the remote
witness committed. It is an unknown delivery outcome, never permission to send a
replacement history. Retrying exports/revalidates the same prefix and relies on the
witness's idempotent exact-checkpoint semantics. Cached evidence remains available
only while all established state validates. Missing or damaged evidence requires
administrator investigation and restoration from independently authenticated material;
there is no reset, automatic repair, credential rotation, or cap-raising command.
Directory locking rejects concurrent local admin operations without waiting.

## Status contract

| State | Meaning |
| --- | --- |
| `disabled` | No marker and no enrollment directory; no network attempt |
| `initialized` | Offline genesis evidence was durably installed |
| `cached_receipt` | Saved signature verified; no claim about current remote state |
| `published` | Exact captured target receipt and challenged head verified, evidence durably saved |
| `fresh_match_at_snapshot` | New challenge matches the captured authority target |
| `fresh_witness_behind` | New challenge matches a verified earlier local checkpoint |
| `fresh_witness_ahead` | Witness exceeds captured authority; possible local rollback, investigate |
| `conflict` | Regression behind saved evidence or a conflicting checkpoint/receipt |
| `unavailable` | Bounded child/network operation or returned evidence failed |
| `configuration_error` | Missing, partial, unsafe, invalid or mismatched protected state |

Errors and `fresh_witness_ahead` return nonzero. Fresh results include the authority
target, witness receipt and local capture/observation times. Those times are descriptive,
not remote attestations. Every fresh query uses a new parent-selected nonce. Fresh
status never falls back to cached success and never changes persistent evidence:
the durable regression floor is the last successful publish, not an earlier transient
fresh observation. Cached status does not contact the witness or export the live head.

This is a checkpoint delivery boundary, not a general trusted-host firewall. Installed
code, root, the signer UID and the publisher's own UID remain trusted within their
respective roles. Root can change both code and enrolled policy. Coherent rollback or
deletion of all local state, including marker and evidence, cannot be detected locally;
only an independently retained witness and authenticated fresh query can provide
outside evidence. Signatures do not prove truthful event contents, consent, physical
action, off-machine placement, independent administration or an enduring safe state.

## Verification scope

Host tests use throwaway loopback mTLS certificates and real authority/witness
journals. They exercise enrollment, descriptor/mode checks, signed replies, hostile
child results, replay, complete growing-prefix export, rollback/conflict, lost replies,
bounded subprocesses and fsync failures. Root ownership is simulated on the host;
these tests do not establish Linux kernel isolation.

Three explicit Linux tests exercise real static UIDs/groups, no-network signer export,
publisher denial of a synthetic signer key/socket, and full network-worker exec with
Landlock and loopback mTLS. They require the future image,
root and deliberate opt-in, and are skipped on the Mac host:

```sh
EXO_WITNESS_LINUX_ACCEPTANCE=1 /opt/exo/bin/python -m pytest \
  tests/test_witness_linux.py \
  tests/test_witness_publisher.py::test_real_network_worker_landlock_and_exec_roundtrip -q
```

Real image build/boot, full worker Landlock execution, power-loss/old-image persistence
and independent remote deployment remain separate acceptance work. No current-image
or independent-witness deployment claim follows from host test success.

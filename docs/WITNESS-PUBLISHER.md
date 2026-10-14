# Optional administrator witness delivery

`exo-witness-admin` exports a protected authority's checkpoint history to one
explicitly enrolled witness. It is disabled by default. The current source pins
merged core main `859fbcc98e3fe9cc3af31cdc9d6b130db718e972`, which includes witness
transport and its public challenged-head API. It does not activate a witness in
an existing image, deploy a remote service, or establish independent administration.

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

Before publication, a query-only network child receives an empty checkpoint spool
and a new parent-selected nonce. The root parent verifies the signed challenged
head against the pinned identity, witness key and nonce, then compares it with the
saved receipt and complete captured authority prefix. A regressed, conflicting or
ahead witness refuses publication before any export is sent to a publication child.
A successful preflight permits a separate network child to publish the captured
prefix and query the resulting head with a distinct parent-selected nonce.

The parent independently verifies the child's publication receipt against the exact
pinned checkpoint and verifies this second signed head envelope itself. Only an
exact matching challenged head permits local receipt publication. If another publisher advances the witness during delivery,
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

Four Linux kernel cases exercise real static UIDs/groups, no-network signer export,
publisher denial of a synthetic signer key/socket, full network-worker exec with
Landlock and loopback mTLS, and an installed worker in a separate session killed
by Linux when its exact parent terminates. The parent-death test uses the fixed
publisher UID, verifies the installed SIGKILL guard, and adopts/reaps the worker
with bounded readiness and fallback cleanup. They require the future image,
root and deliberate opt-in, and are skipped on the Mac host. Use a separate test
runner with pytest and cryptography, with read-only access to the installed core/OS
packages; production `/opt/exo` does not need test dependencies:

```sh
EXO_WITNESS_LINUX_ACCEPTANCE=1 /run/witness-test-runner/bin/python -m pytest \
  tests/test_witness_linux.py::test_real_uid_split_denies_export_network_and_publisher_signer_access \
  tests/test_witness_publisher.py::test_real_network_worker_landlock_and_exec_roundtrip \
  tests/test_witness_linux.py::test_installed_separate_session_worker_dies_with_exact_parent -q
```

A fifth opt-in case exercises the complete installed admin flow against the boot
authority's existing nonempty history. The outer boot harness must verify the raw
image SHA256, start a disposable overlay with a new VM UUID, and supply all five
explicit expectations below from its retained build evidence. `SOURCE_SHA256` is
the installed manifest's source digest, not the raw disk digest. The guest verifies
schema 2, `vmtest`, OS commit, core pin, source digest, and sysfs VM UUID before
creating a fixture. The raw digest is retained as a **host-harness attestation**;
the guest cannot prove that immutable disk digest after overlay boot.

```sh
EXO_WITNESS_LINUX_ACCEPTANCE=1 \
EXO_WITNESS_EXPECTED_IMAGE_SHA256="$verified_raw_image_sha256" \
EXO_WITNESS_EXPECTED_SOURCE_SHA256="$built_source_sha256" \
EXO_WITNESS_EXPECTED_CORE_PIN="$built_exo_pin" \
EXO_WITNESS_EXPECTED_OS_COMMIT="$built_exo_os_commit" \
EXO_WITNESS_EXPECTED_VM_UUID="$EXO_OS_VM_UUID" \
  /run/witness-test-runner/bin/python -m pytest -q -s \
  tests/test_witness_installed.py::test_installed_admin_exports_provisioned_authority_and_persists_fresh_witness
```

The runner generates synthetic mTLS material in its own private root-owned `/run`
directory. Its probe uses `/opt/exo/bin/python -I -B` and checks that runtime imports
resolve inside `/opt/exo`; it imports no editable runtime source or test dependencies.
A public-only client drops to the static `exo` identity to query signed genesis and
head over the authenticated authority socket. The installed admin then initializes
offline, executes the real no-network signer export and separate Landlock publisher,
and verifies persisted previous/current evidence and two fresh status challenges.
The synthetic server records the native signed challenge envelopes; the test verifies
their signatures and five distinct nonces: parent prepublication, client preflight,
parent postpublication, and two fresh status queries. Both prepublication heads
retain the prior receipt; the remaining three match the published receipt. The
evidence also names each phase in `signed_head_observations`. Each command and
server shutdown has a finite deadline. Live authority growth may honestly produce
`fresh_witness_behind` after publication.

The outer installed-flow process has one 700-second total deadline, including a
reserved 16-second cleanup interval. Stdout is capped at 64 KiB and stderr at
8 KiB; neither child stderr nor invalid response bodies are copied into errors.
Failures retain only a fixed step, whitelisted error class, bounded CLI exit code
and known admin-state enum. A `conflict` refusal remains visible while the probe
returns nonzero. The outer runner validates this diagnostic record before showing
it; arbitrary response fields, messages, paths and stderr remain redacted.
Probe and CLI commands share the outer runner's new process group. Cancellation
interrupts the active CLI collector and server cleanup. The runner kills the
shared group if needed and reaps the probe process. Installed workers use separate sessions with
`PR_SET_PDEATHSIG(SIGKILL)` and a parent-identity race check. Host tests verify
the shared-group cleanup and guard calls; the separate-session kernel test
remains an opt-in Linux check. This does not establish containment of arbitrary
host processes in unrelated sessions.

An existing enrollment directory or marker, including partial state or a dangling
symlink, refuses this test. Only its exact private `/run` fixture is cleaned up;
the newly created persistent enrollment is deliberately left for the harness to
discard with the overlay. Run it last on a fresh disposable VM. Its single JSON
evidence record is printed with `-s`; retain it with the outer raw-image hash and
boot UUID proof. This synthetic loopback test establishes no independent endpoint.

The historical `c2d1ad9` image, with core `cff62d1`, has build and read-only audit
evidence. Those results do not validate the current source composition. Installed
witness acceptance remains pending. The four kernel cases and one installed-flow case, receipt persistence through
normal and older-root reboots, full worker Landlock execution, power-loss behavior
and independent remote deployment remain separate checks. Unrelated historical
VM suites do not establish witness behavior. See
[VALIDATION.md](VALIDATION.md#current-image-evidence).

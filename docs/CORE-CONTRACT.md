# The boundary between Exo and Exo OS

Exo OS makes the person's authority a property of the running machine. A model
can propose an act; a trusted mechanism decides whether that act may happen;
the machine restricts what the act can touch; the result becomes evidence the
person can inspect and carry to another machine. An attractive desktop alone
does not establish any of those properties.

The base Exo repository is developed independently. OS work uses the exact
commit in `exo.pin`, never modifies a developer's Exo checkout, and never copies
the core into an OS fork. The current plugin, protocol-client, and ledger tests
are the compatibility checks before moving that pin.

The current source composition pins merged core main
`8600bce83b1e43b4a33e0957cb6b723c20204ea3`, including witness transport PR 88,
the required executor and model-spend boundary. It requires its own image and
native acceptance evidence. The schema3 input-probe proposal remains pending
peer review. Host and private GTK checks do not establish image acceptance.

The separate foundation batch source `4aec0b9` used merged core
`3ea58f46e3d3b38cb7f2fdfe24e1ca8416dd8ff3`, including core PRs 91 and 87.
That OS adapter through PR 41 supplied the required trusted-folder contract.
Witness transport was outside that earlier pin. Its Linux ARM host suite passed
545 tests with 12 skipped. Its privileged kernel and native GTK checks were
unverified. Those results do not transfer to the current source.

## Responsibility and acceptance

| Principle | Exo owns | Exo OS owns | Evidence required before daily use |
|---|---|---|---|
| The person chooses ends | Principal identity, authenticated instructions, consent gate | Trusted client and OS isolation of agent tools | A tool cannot impersonate the principal or answer its own approval |
| Attention is scarce | Risk classification and interruption policy | Calm surface, explicit defer, recovery after interruption | Lost replies cause reconciliation; consent is never automatically resent |
| The signed record is truth | Durable authority journal, read-only verification, portable store and independent witness protocol | Protected signer identity, persistent history, root receipts and projection recovery | Altered/missing copies are detected; recovery repeats no operation; independent witness retains acknowledged history |
| Reversibility governs freedom | Gate and action classification | Checked profile changes, snapshots, rollback | Broken changes revert; an older system actually boots, with home/store intact |
| Private data stays private | Credential broker and tool dispatch contract | OS-enforced filesystem, process and network restrictions | A hostile tool cannot read keys, principal credentials, browser data, or contact arbitrary hosts |
| Mechanism is separate from person | Generic core and data-only principal store | Image/service packaging and generated machine configuration | A fresh machine opens the same store and verifies its ledger |
| The machine remains usable | Replayable protocol and honest errors | App keys, compositor and recovery path independent of exod | The desktop still launches apps with exod stopped |

## Compulsory execution in the pinned core

The pinned core provides an installed `exo.executors` interface. With
`EXO_TOOL_EXECUTOR_REQUIRED=1`, startup refuses a missing executor or required
plugin, and every registry call crosses that executor after the gate and schema
validation. The request's call identifier and detached arguments are immutable.
The OS adapter isolates foreground shell and file/search/patch tools; it refuses
background jobs, unknown tools, MCP startup and remote delegation. Local worker
dispatch retains the same registry boundary. These are explicit refusals until
reviewed adapters exist, not routes around the boundary.

The operator's root-owned policy fixes the working set, runtime and executor;
principal plugin-directory overrides cannot replace the required plugins.
Enforcement failure is a refusal, with no unsandboxed fallback. Host Git
inspection of tool-writable repositories is disabled in handoff and completion
because repository configuration can execute commands. Credentialed HTTPS
actions use a separate service after exact one-shot approval; tool children
have no network or credential-store access. The approved arguments include
the expected origin, and the service checks it against its fixed policy before
sending. General API response adapters remain separate work.

Exo OS supplies bubblewrap namespaces, Landlock, seccomp, bounded resources and
process-tree cleanup. The Linux tests attempt direct socket, filesystem,
`/proc`, environment, descriptor and detached-process bypasses. Booted-image
acceptance is recorded separately from these container results. See
[TOOL-ISOLATION.md](TOOL-ISOLATION.md).
The service's durable request history lives outside system snapshots, preventing
rollback from forgetting an uncertain outgoing action. Its narrower response
contract and limits are in [CREDENTIAL-EGRESS.md](CREDENTIAL-EGRESS.md).

Root receipt verification is implemented separately in
[SYSTEM-TRUST.md](SYSTEM-TRUST.md). Group membership still permits a same-user
process to invoke the five root operations. Isolated tool processes cannot reach
that socket, while installed host code and other desktop applications remain
trusted. A signed receipt is evidence of execution, not independently a proof of
human consent.

The required ledger backend now owns its keys and history under a separate OS UID.
Core gate audit commits must succeed before an action, policy acceptance or durable grant becomes
active. Each pending approval binds a distinct call, even when another session asks for identical
arguments. The OS provisions a fresh signer before login, validates established state on later boots,
and preserves it on `@signer` across system recovery. Canonical `exo log verify` and `exo memory verify`
compare the local projection to a captured signed authority head without repairing evidence; an
explicit writer synchronization can restore missing committed rows. Unknown append outcomes retain
their operation ID and require lookup. See [LEDGER-AUTHORITY.md](LEDGER-AUTHORITY.md).

The model-spend contract belongs in the shared core: protected policy selection,
exact route pricing, durable reservations, global stop behavior, and the
`models.list` status. The OS fixes a root-owned policy path, provisions the
exo-owned journal on persistent `@store` after the signer and before login,
and denies tools access to both files. The installed policy starts at zero
daily/monthly paid spend with no routes. The deterministic offline model remains
usable. This is local routed-call accounting under trusted installed code; it
does not control every same-UID process or the provider's account-wide bill.
See [MODEL-SPEND.md](MODEL-SPEND.md).

Image `c2d1ad9` with core `cff62d1` has build and read-only audit evidence.
Acceptance is held. Phase 3 failed both attempts. No complete native suite has
passed. The remaining six suites and five witness cases are not run. Historical
results apply only to their named images. Exact identities and limits are in
[VALIDATION.md](VALIDATION.md#current-image-evidence).

The pinned core includes the optional witness state machine and transport. It accepts
signed checkpoints and commits a signed acknowledgment before returning it. The OS
stages an explicit administrator publisher, disabled by default, that delivers a
verified checkpoint prefix and distinguishes saved receipts from challenged fresh
status. Linux and image acceptance have not run for this publisher, and an endpoint
under independent control has not been deployed. A copied checkpoint proves its
signed prefix; it does not prove that no later history exists. See
[WITNESS-PUBLISHER.md](WITNESS-PUBLISHER.md). Current-image acceptance and the
remaining phase-4 criteria still apply.

## Protocol improvements from core

The surface uses `hello.seq`, `approvals.list`, event replay and
principal-authenticated `approval.respond` at the tested pin. These support
recovery without replaying a person's decision. Three additions were needed in
the shared protocol and client rather than patched into the OS. All three are in
core at the current pin:

1. An atomic pending-approvals snapshot with its event cursor. `approvals.list`
   now returns an await-free cursor. The surface still reconciles with
   `hello.seq` and does not read that cursor yet.
2. Per-subscription heartbeat. The core pings an idle event stream every 15
   seconds, `hello` reports the heartbeat and the launch instance, and the
   Python client ends a stream that brings no event or heartbeat for 45 seconds.
3. Peer authentication before disclosing a principal credential (Exo D-060,
   PRs 109, 175 and 325). A principal client checks the kernel peer of each
   Unix connection against the launch record before the credential goes on it.
   The core stamps the principal only for the same user and the recorded process.

The surface gets 2 and 3 by joining as `Client(principal="surface")`. The OS
still has to keep the socket out of reach of tools (see BANKAI.md I-1, P-5).
Current behavior and limitations are documented in
[SURFACE-RECOVERY.md](SURFACE-RECOVERY.md).

## Path to a finished OS

After current-image security and recovery acceptance, the ordered exits remain: real-model VM
chat within an explicit spend cap; assistive-technology and
memory-budget checks; voice and phone as clients of the same session; a spare
machine soak; then a reversible trial on the working machine. Hardware trials,
payment approval channels, and multilingual speech require actual evidence.
The roadmap records them as unfinished until those checks have run. Repository
tests or a container running GTK do not constitute an image boot or a daily-use
trial.

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

## Responsibility and acceptance

| Principle | Exo owns | Exo OS owns | Evidence required before daily use |
|---|---|---|---|
| The person chooses ends | Principal identity, authenticated instructions, consent gate | Trusted client and OS isolation of agent tools | A tool cannot impersonate the principal or answer its own approval |
| Attention is scarce | Risk classification and interruption policy | Calm surface, explicit defer, recovery after interruption | Lost replies cause reconciliation; consent is never automatically resent |
| The signed record is truth | Principal ledger, validation and portable store | Independently signed root receipts and repair of missing ledger copies | A substituted/replayed root receipt is rejected; recovery repeats no operation |
| Reversibility governs freedom | Gate and action classification | Checked profile changes, snapshots, rollback | Broken changes revert; an older system actually boots, with home/store intact |
| Private data stays private | Credential broker and tool dispatch contract | OS-enforced filesystem, process and network restrictions | A hostile tool cannot read keys, principal credentials, browser data, or contact arbitrary hosts |
| Mechanism is separate from person | Generic core and data-only principal store | Image/service packaging and generated machine configuration | A fresh machine opens the same store and verifies its ledger |
| The machine remains usable | Replayable protocol and honest errors | App keys, compositor and recovery path independent of exod | The desktop still launches apps with exod stopped |

## Core support needed for phase 4

The next security milestone needs a single compulsory execution boundary in the
core. It must cover foreground shell, background jobs, file/search/patch tools,
MCP subprocesses and delegated workers. An optional new sandbox tool would leave
the existing paths open and does not meet the acceptance condition.

The core should pass a validated operation to a broker with an immutable call
identifier, authenticated actor, exact arguments, approved working set and
network/credential scope. Consent is bound to those values. OS enforcement
failure is a tool refusal; there is no unsandboxed fallback. The broker injects
credentials into authenticated outbound calls, so tool children never receive
the credential store. Agent-controlled configuration cannot lower the operator
floor or replace the broker.

Exo OS supplies the Linux execution adapter, root-owned service policy, private
runtime paths and image dependencies once that core contract is implemented.
The acceptance suite must attempt direct socket, filesystem, `/proc`, network,
and background-process bypasses. A namespace wrapper around only the shell is
insufficient evidence for the whole tool system.

Root receipt verification is implemented separately in
[SYSTEM-TRUST.md](SYSTEM-TRUST.md). Group membership still permits a same-user
process to invoke the five root operations. Until the compulsory broker/isolation
boundary exists, a signed receipt is evidence of execution, not proof of human
consent. Phase 4 remains incomplete.

## Protocol improvements needed from core

The surface currently uses `hello.seq`, `approvals.list`, event replay and
principal-authenticated `approval.respond` at the tested pin. These support
recovery without replaying a person's decision. Three additions should be
implemented in the shared protocol/client rather than patched into the OS:

1. An atomic pending-approvals snapshot with its event cursor, so the client can
   reconcile without the current snapshot/event ordering workaround.
2. Per-subscription heartbeat or acknowledged stream progress. Command health
   alone cannot prove that an entirely idle event connection is alive.
3. Peer authentication before disclosing a principal credential, consistently
   for every client. A filesystem socket path is not a daemon identity.

These are integration requirements, not interfaces already present in the pin.
Current behavior and limitations are documented in
[SURFACE-RECOVERY.md](SURFACE-RECOVERY.md).

## Path to a finished OS

After the compulsory security boundary, the ordered exits remain: real-model VM
chat within an explicit spend cap; bootable rollback; assistive-technology and
memory-budget checks; voice and phone as clients of the same session; a spare
machine soak; then a reversible trial on the working machine. Hardware trials,
payment approval channels, and multilingual speech require actual evidence.
The roadmap records them as unfinished until those checks have run. Repository
tests or a container running GTK do not constitute an image boot or a daily-use
trial.

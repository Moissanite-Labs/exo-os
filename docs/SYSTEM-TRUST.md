# System helper trust boundary

`exo-sysd` is the only Exo OS process that runs as root. It accepts five named
operations: `status`, `snapshot.list`, `snapshot.create`, `package.install`, and
`snapshot.undo`. The implementations check argument names and types and execute
fixed argv lists without a shell. Package names cannot become options. Snapshot
undo accepts only a recorded pre/post pair. Pacman and snapper hooks remain
available; this change introduces no service sandbox that would block them.

## Authenticating a root action

Before sending any request, `SysdClient` obtains the Unix socket peer's credentials
from Linux `SO_PEERCRED` and requires uid 0. Socket location and socket file
ownership are not authentication. `EXO_SYSD_SOCKET` may select a socket but cannot
change the expected peer uid or receipt trust anchor. Unsupported peer-credential
platforms fail closed.

The helper publishes only its Ed25519 **public** key, atomically and with mode
0644, at `/run/exo-sysd/receipt.pub`. Its directory is root-owned mode 0755. The
private seed remains at `/var/lib/exo-sysd/sysd.key`, mode 0600, in the helper's
private state directory. The client rejects symlinked, non-root-owned, or
user/group-writable public-key files and ancestors; it verifies receipts against
this independent local anchor, never the key supplied by the reply. The key is
loaded after the bounded reply: on first systemd socket activation, connect can
complete before the daemon has initialized and published its key. Kernel root
peer authentication still happens before sending the request. A root process
can replace this anchor: root and a trustworthy boot/runtime filesystem are part
of the boundary. Rotate the seed deliberately; old receipts require the old
trusted public key to reconcile and do not silently trust their embedded keys.

Tests can explicitly inject `expected_peer_uid` and `trust_key` in a client,
`public_key_path`/`log_path` in a server, and simulated kernel credentials on
macOS. Production CLI/plugin construction uses the defaults. No environment
variable disables authentication or signature verification.

A version-1 request is exactly:

```json
{"version":1,"request_id":"64 lowercase hex characters","action":"snapshot.create","args":{"description":"before change"}}
```

This replaces the earlier unversioned helper protocol. Client and helper must
be upgraded together by rebuilding the image; old requests are refused. The
shared Exo protocol is unchanged.

The client generates a fresh random 256-bit `request_id`. Each named operation,
including reads and errors, requires a signed receipt binding the protocol
version, request id, operation, exact arguments, result, success flag, error,
audit error, execution state, caller uid and pid, timestamp, duration, and public
key. The reply must agree with the signed fields. Altered, absent, substituted,
and replayed receipts produce an untrusted/unknown outcome and are never added
to the ledger. JSON permits neither duplicate keys nor non-finite constants.

The receipt's `execution` field is `completed` for a successful operation,
`not_started` when an unavailable root audit log prevents execution, or
`failed_or_partial` when an operation raises. The last state is deliberately
conservative: pacman or snapper may already have changed state before a later
step fails. An `ok: false` response does not promise that nothing changed.

## Auditing without repeating an operation

Execution, receipt signing, and append to `/var/lib/exo-sysd/audit.log` are serialized
under one helper lock. The log is opened and checked before execution. The helper
refuses an operation if that preflight fails. A write/fsync failure after an
operation succeeds keeps its successful result and signs an explicit
`audit_error`; it does not pretend the operation failed or invite a retry.

For mutations the caller first saves the verified receipt in
`$EXO_HOME/sysd-outbox/<request_id>.json` (0600), using atomic replacement plus
file/directory fsync, then appends a `system.action` row to the principal's
ledger. The outbox is 0700. A missing principal ledger counts as an audit
failure; a null sink is not a recorded action. Mutations cannot request
`record=False`.

If the operation completed but either audit append fails, the client raises
`CompletedButUnrecorded`, with `completed=True`, `retryable=False`, the successful
`result`, the verified `receipt`, and its `outbox_path`. `to_dict()` exposes the
same structured outcome and a direct "Do not retry" message. The CLI and plugin
must preserve this completed outcome instead of turning it into an ordinary
failed tool call. If local outbox persistence also fails, the exception still
carries the verified receipt; the root log ordinarily holds a second copy.
No design can guarantee a user outbox write on a failed/full filesystem.

`SysdClient.recover_pending()` verifies saved receipts using the same pinned
public key, matches caller uid and filename/request id, and appends only missing
`system.action` rows. It returns lists named `recorded`, `already_recorded`,
`rejected`, `errors`, and `root_audit_errors`. It never connects to the helper or
reruns an operation. Deduplication accepts only a matching, root-verified receipt
already in a system-action ledger row. Normal append and reconciliation share an
outbox file lock, preventing duplicate appends between these clients. Files are
retained so reconciliation is repeatable after a crash. Reconciling the user
ledger cannot repair a failed root log append; that remains explicit in the
summary and receipt.

## Resource limits and remaining scope

Requests are limited to 64 KiB and replies to 256 KiB. The helper reads one line
with a five-second absolute deadline, limits active connections/worker threads
to eight, and bounds writes by a five-second socket timeout. Excess connections
are closed without work. Actions run one at a time. A response too large to send
is closed rather than truncated; the caller reports an unknown outcome and must
check system state before retrying. The client bounds response size and has a
configurable response deadline (1800 seconds by default).

Fixed subprocess commands retain an 1800-second per-command timeout. Their
existing `subprocess.run(capture_output=True)` can accumulate unbounded command
output in memory; socket limits do not solve that separate resource gap.

A broken connection, lost response, process crash, or failure within a multi-step
package operation can leave an unknown or partial outcome. The nonce prevents
receipt replay; it is not an exactly-once command journal and does not stop a
caller from submitting a new mutation. The server log is local durable audit,
not a tamper-proof remote witness. The client does not automatically retry
mutations.

Socket group membership remains authority to invoke these named operations.
This boundary authenticates the root helper and its audit statements; it does
not enforce the Exo consent gate inside sysd. Arbitrary code running as the same
user, including a tool that bypasses the plugin, can connect to the socket and
request a named operation. Enforcing gate-only invocation requires the separate
core broker or sandbox. Neither a pinned receipt key nor signed receipts provide
that missing authorization boundary.

The tests cover named fixed-argv actions, kernel-peer mismatch before sending,
missing/forged/altered/replayed/mismatched receipts, malformed and oversized
JSON, read deadlines, worker-slot release, key publication, root-log failures
before and after execution, user-ledger failures with durable receipts, and
reconciliation against an actual principal ledger. macOS tests explicitly
simulate peer credentials. `tools/test-linux.sh` also runs an unprivileged client against a root helper
using actual Linux SO_PEERCRED and the default root-owned public-key path. The image's systemd socket
activation and real pacman/snapper operations still require a fresh VM build and boot.

The CLI preserves a completed audit fault in JSON and exits 3, distinct from an ordinary failure.
`exo-os system reconcile` (or `exo-os --json system reconcile`) records saved receipts only; it exits
nonzero while rejected receipts or unresolved audit faults remain. The plugin returns the structured
completed outcome, including the message to show the person, rather than a retryable tool error.

The fresh image mounts `/var/lib/exo-sysd` from its persistent `@sysd` subvolume.
The helper requires that mount and asserts it is a mount point before starting.
Its signing identity and root audit log therefore remain current when an
administrator boots an older system checkpoint. This changes the fresh-image
layout only; existing installations need an explicit state migration.

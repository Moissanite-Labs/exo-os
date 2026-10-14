# Approved credentialed actions

`credential_request` is trusted Exo OS control code, registered high risk. Its
name deliberately maps to the pinned core gate's **credentials** category:
mandatory exact argument binding, one-shot consent, no standing grants, and
approval even in observe mode. Gate decisions and tool outcomes use Exo's normal
principal ledger and events. A denied call never reaches the egress client.

The approval summary names the HTTP method, path and destination. Its literal JSON
preview shows all request metadata before the complete body, so a large body cannot
hide the operation. Control characters remain escaped without changing the bound
request. Unknown or malformed fields stay visible and prevent approval.

The action service does not give tool children network access. Bubblewrap,
Landlock and seccomp still deny sockets and expose neither broker sockets nor
credential paths. Unknown network tools remain refused. Human browser and
terminal applications outside that namespace remain trusted desktop programs;
this does not constrain their direct network traffic or separate hostile
same-user desktop applications from the principal daemon.

## Two principals, two kernel checks

`exo-egress.socket` is created by systemd/root at `/run/exo-egress.sock`, mode
0660, group `exo-sys`. Its separate service runs as the static `exo-egress`
account, with no root capabilities, read-only system/home isolation, private
scratch space, four workers, and persistent private state. It is not exo-sysd
and exposes no root action API.

Before sending any payload, the client verifies the socket and all ancestors,
root account information, and root **SO_PEERCRED** of the activated listener.
An inherited listener reports its creator's UID, not the accepting service UID.
The client therefore also enables **SO_PASSCRED** and checks kernel-provided
**SCM_CREDENTIALS** on the bounded greeting against the `exo-egress` account
from root-controlled `/etc/passwd`. Missing, truncated, duplicate or unexpected
ancillary data is rejected; received descriptors are closed. The service checks
SO_PEERCRED of the connecting client against the fixed `exo` desktop UID. A
fresh nonce binds every accepted reply. Neither side trusts a JSON identity.

## Administrator provisioning

The shipped `/etc/exo-os/egress.toml` has empty `[profiles]`: no upstream grant.
Only an administrator may change the policy or install a credential drop-in.
The policy and all ancestors must be root-owned, non-writable by group/others,
and free of symlinks. Policy is loaded once on service startup. For example,
using an example origin rather than a live paid API:

```toml
[profiles.example_action]
origin = "https://actions.example.invalid"
methods = ["POST"]
path_prefix = "/v1/action"
credential = "example-token"
```

An administrator provisions an encrypted systemd credential outside Exo/model
control, then adds **only** its specific entry to a root-owned service drop-in:

```ini
[Service]
LoadCredentialEncrypted=example-token:/etc/credstore.encrypted/example-token
```

No credential file or account key is shipped. The service reads the named
credential from systemd's `$CREDENTIALS_DIRECTORY`, verifies protected paths,
and injects `Authorization: Bearer ...` internally. No tool request accepts
headers, auth, host, scheme, proxy configuration, or a credential path.
Configuration changes require an administrator's service restart. The tool's
required `expected_origin` is an approval assertion: the broker refuses it
unless it matches the loaded profile's exact origin; it never uses this field
to choose a destination. Thus changed policy cannot silently substitute a
request's approved destination.

## Narrow HTTP contract

A request contains profile, expected_origin, method, path, optional UTF-8 body,
and a caller-stable request_id. Its canonical path starts with `/`; percent
encoding, query strings, fragments, backslashes, repeated slashes, and dot
segments are refused. A scoped prefix matches exactly or at a slash boundary.
This conservative grammar prevents alternate decoding and authority/prefix
escapes; query-based APIs require a separately reviewed adapter.

Requests have at most 32 KiB of body. HTTPS validates both certificate chain and
hostname. The service resolves each administrator origin once during startup
and uses a fixed address for that process lifetime; startup DNS failure refuses
startup, and libc DNS resolution is outside the request deadline. No environment
proxy is consulted. Redirects are never followed. A ten-second request deadline
bounds connection/TLS and HTTP header/body processing; a transport watchdog
prevents byte-at-a-time header delivery from occupying workers indefinitely.
Responses are drained only up to 64 KiB. JSON wire messages are bounded and
reject duplicate keys and non-finite numbers; socket I/O is bounded.

**All upstream response bodies, headers, reason phrases and exception details
are withheld.** Returning arbitrary upstream text cannot guarantee secrecy: an
endpoint can echo its injected token with arbitrary encoding. The result is
HTTP status and outcome metadata, suitable for credentialed actions. Numeric
status establishes an HTTP response, not semantic success of the requested
operation. General read APIs need separately reviewed response adapters.
The service does not log or save plaintext request/response bodies or
Authorization. The proposed request is still part of Exo's normal tool and
approval history; this is not a promise to erase the person's approved payload
from session storage. Credential values are supplied only by the service.

## Durable outcomes and recovery

The caller must keep a stable request_id for one exact operation. Before any
HTTPS attempt the service commits an ID and request fingerprint to SQLite with
FULL synchronization. The state directory `/var/lib/exo-egress` is mounted from
persistent `@egress`, outside system `@` snapshots/recovery; the unit requires
that mount before starting. At 100,000 IDs the broker refuses new operations
rather than evicting idempotency protection.

- The same ID with different approved arguments is refused.
- A duplicate completed operation returns stored metadata saying it already
  completed; its original response payload was not retained.
- A duplicate in-flight operation returns **unknown, nonretryable** and is never
  sent again, including after service restart, lost client reply, or audit error.
- A transport failure after any request bytes may have been sent returns unknown;
  there is no automatic retry. This applies to reads as well as mutations.

External state may change before a reply or principal ledger row is recorded.
Retrieving an outcome uses the original ID and exact arguments after fresh
approval. Never change an ID to retry an unknown operation; inspect the upstream
through a human-approved recovery procedure. This is local duplicate suppression,
not an upstream transaction or proof that a business action happened exactly
once. The administrator must preserve `@egress` across restores; deleting its
journal removes the protection.

## Evidence

`tests/test_egress.py` uses a generated TEST certificate/key and a real local TLS
fixture. It checks secret delivery only upstream, complete payload withholding,
verified TLS, route/path denial, no redirect following, request/response bounds,
header deadlines, duplicate races, persistent in-flight state, lost upstream
reply, and durable-outcome failure. Tests never contact a cloud service or use
existing credentials. Plugin tests run the pinned Exo gate and confirm one
exact one-shot prompt per call, including observe mode, before dispatch.

`tests/test_egress_peer.py` has portable authentication simulations and a real
inherited-listener/SO_PEERCRED/SCM_CREDENTIALS test requiring a disposable root
Linux runner. A Mac pass does not establish that kernel check. The existing
sandbox Linux suite remains the evidence for child network/socket isolation.
Booted-image mount persistence, service hardening and adversarial end-to-end
acceptance remain separate checks; no live configured API authorization or
credential injection is claimed by these host fixtures.

# Compulsory tool execution on Linux

The image requires the installed `exo-os` executor on every Exo registry call.
The shared core validates arguments after its consent hooks, then hands an
immutable request to the executor. Missing executors, policy, required plugins,
or enforcement mechanisms refuse startup or execution. No error falls back to
the original host handler.

The implementation uses the shared `exo.executors` interface on the exact
commit in `exo.pin`; the implementation remains in the shared core repository.

## The working set

The administrator's root-owned `/etc/exo-os/tools.toml` grants one directory:
`/home/exo/Workspace`. Files placed here are available to tool code. The
directory cannot be a symlink; a requested working directory must resolve
inside it. Exod starts there. Tools cannot enlarge the working set or edit
the policy.

File reads, writes, edits, patches, search and foreground shell commands run
in a new bubblewrap user, mount, process, network, IPC and UTS namespace. The
runtime is read-only; the home, store, SSH credentials, browser profile,
compositor, bus and root-helper sockets are absent. Temporary home and runtime
directories are new for every call. No host environment or file descriptors
are passed through.

Before dispatch, the child applies Landlock ABI 3 filesystem restrictions and
a libseccomp filter. Network socket creation and connection, cross-process
inspection, namespace changes and selected kernel interfaces are denied.
The event loop retains only its own private socketpair for thread completion;
that pair has no connection to the host. If any enforcement step fails, the
tool handler does not run. Limits bound execution time, result size, address
space, files, open descriptors and child processes. Timeout or cancellation
destroys the process namespace, including detached descendants.

This policy intentionally gives tools **no network access**. Approved credentialed
actions use the separate, administrator-configured `credential_request` control
tool after exact one-shot approval, never a tool-child network grant. Empty
shipped policy refuses all routes. See [CREDENTIAL-EGRESS.md](CREDENTIAL-EGRESS.md).

## Trusted host operations

The fixed image contains trusted Exo/gate/OS Python code. Its narrow control
operations still run in the daemon after the gate: UI panels, checked machine
settings and undo, existing app launch, system receipt operations, credentialed actions and timeline
queries. These receive structured arguments; they are not general executable
or file-path adapters.

Because the compositor runs outside the tool namespace, every profile apply
(including undo) and app launch rejects arbitrary command definitions when
the executor is required. Only the exact `chromium` and `foot` commands map to
fixed `/usr/bin` executables. Web apps require HTTPS without URL credentials
or control characters. Launches use `/` as cwd and a minimal desktop
environment. Model-driven app definition writes remain refused.

Unknown tools, background job requests, MCP startup and remote delegation are
refused until they have reviewed adapters. Existing local delegated calls
still traverse the same registry. Git inspection used by handoff and filename
completion is disabled in required mode: running Git on a tool-writable repo
could execute its `core.fsmonitor` command with host authority.

Required-mode plugin discovery uses only executor-selected root-controlled
folders. Ambient plugin entry points and principal folder overrides are disabled.
The executor itself comes from its named installed factory. Required gate/OS
plugins cannot be disabled.
Installed providers, plugin Python, protocol commands and browser/terminal
applications are trusted host code, not hostile payloads contained by this
sandbox. Human administrator access, a compromised daemon, kernel exploits
and intentionally exposed sensitive workspace files are outside this claim.

## Verification and remaining work

`tests/test_sandbox.py` checks fail-closed routing and desktop constraints.
`tests/test_sandbox_linux.py` runs actual kernel isolation, network refusal,
secret-path/symlink hiding, descriptor isolation, timeout and cancellation
checks. It requires an explicitly enabled disposable Linux runner with user
namespaces, Landlock and libseccomp. A container host that prohibits nested
user namespaces will correctly refuse tools; it does not establish a pass.

Run the VM suite with
`EXO_OS_VM_ACCEPTANCE=1 tools/accept-isolation.sh --vm-acceptance --image-source FULL_BUILT_OS_COMMIT`.
It records the clean harness revision separately and retains the image manifest,
core pin, runtime/image digest and VM UUID checks. The model-spend denial probe
requires four exact read/append-open outcomes and known denial errno, plus a
successful measurement-process exit. Bash reports shell exits as `[exit N]` text;
its tool `is_error` flag alone proves neither access nor denial. The report retains
safe path/operation/errno evidence, never state contents. A tools-only rerun leaves
the earlier failed report intact and establishes a result only if all checks pass.

Credentialed action egress has local TLS fixture evidence; P4 still requires
booted-image egress acceptance, an off-machine audit anchor and an adversarial
booted-image check. The separate signer has historical ledger/recovery evidence.
The older `5a86e3b` isolation suite failed 1 of 13 checks because its spending
probe assumed Bash's `is_error` meant access was allowed. The raw probe outcome
was not retained, so that run confirms neither denial nor disclosure for spending
state. The corrected harness and fresh `c2d1ad9` image need VM acceptance. See
[results and limits](VALIDATION.md#historical-image-5a86e3b). A named
root receipt proves execution; compulsory tool isolation prevents a tool from
reaching that socket, but the principal's other same-user applications remain
trusted and can reach the named root API. Full user/process separation has
not been claimed.

References: [bubblewrap's sandbox responsibility](https://github.com/containers/bubblewrap#sandbox-security),
[bubblewrap options](https://github.com/containers/bubblewrap/blob/main/bwrap.xml),
[Landlock kernel interface](https://docs.kernel.org/userspace-api/landlock.html).

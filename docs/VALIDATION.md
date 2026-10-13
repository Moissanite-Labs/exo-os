# Runtime and isolation validation

Checks on 2026-10-13. The current Exo dependency is
`b3149a91d2981d1a6f71fdb2400b99b1f8e21499`, as recorded in `exo.pin`.
Integrated OS `ee327a8` has the same source tree as published `8d79fdd`.
Results below name the revision they exercised. Source and container checks do
not establish a booted current image or a successful recovery reboot.

| Check | Result | Boundary of evidence |
|---|---|---|
| macOS arm64, Python 3.13, `.venv/bin/pytest -q` | 396 passed, 16 skipped | Integrated OS `ee327a8`, core `b3149a9`; GTK, Hyprland and actual Linux kernel checks are unavailable on this host |
| Linux arm64, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 373 passed, 10 skipped | Authority branch before its final systemd ordering/environment assertions and the additional integrated VM harness tests; includes actual inherited-listener and signer service identity checks |
| Linux arm64, Ubuntu kernel 6.8, unprivileged UID 65534, `tests/test_sandbox_linux.py` | 9 passed | Earlier OS `5773dc4` and core `a915e62`; actual bubblewrap, Landlock and seccomp in a disposable privileged container with no host mounts |
| Shared core authority, witness and read-only verification | 82 passed | Core `b3149a9`; durable commits, replay, independent receipt validation, canonical CLI corruption checks and concurrent verification |
| Full core suite and plugins, macOS arm64, Python 3.13 | 1,434 core tests and 749 plugin tests passed | Core `b3149a9`; 65 core skips and one explicitly deselected external legacy-relay integration; all six plugins also passed separately |
| systemd unit dependency graph | passed | Provision/socket/service/firstboot graph checked in Linux with unavailable image executable paths replaced only in temporary validation copies; actual first boot remains necessary |
| Ruff `--select F`, `src tests` | passed | Python static checks |
| `bash -n tools/*.sh` | passed | Shell syntax, not a VM acceptance run |
| `git diff --check` | passed | Whitespace/conflict-marker check |

The full core run used an isolated checkout and a closed local bus address. The
legacy-relay test was deselected because it autodetects an unrelated local
checkout. The inherited `NO_COLOR=1` setting initially caused four TUI tests to
fail by selecting the accessible plain theme. All 31 tests in that UI file and
then the full suite passed with `NO_COLOR` and `EXO_REDUCED_MOTION` unset for the
test process. No application setting was changed. GitHub Actions jobs for core
PR 80 did not start because of an account billing restriction; these are local
results, not a CI pass.

The Linux runner creates a disposable container with two CPUs and 2 GiB of RAM.
It copies an explicit source list and archives the pinned core commit, installs
the two packages, and destroys the container on exit. It mounts no host folder,
credential directory, socket or desktop. GTK widgets use a private Broadway
display. An explicit container-only test runs an unprivileged client against a
root helper with actual Linux kernel peer credentials and the default
root-owned public-key path. macOS tests simulate those Linux credentials.

The nine isolation checks ran with no supplementary groups. The dedicated
Colima VM's AppArmor restriction on unprivileged user namespaces initially
caused bubblewrap to refuse startup. For that explicit disposable test only,
the restriction was disabled and then restored; refusal was checked again
after restoration. The image policy was not weakened. The runner never changes
a normal host's kernel policy to obtain a pass. An Arch-image boot must verify
the actual deployment kernel separately.

For comparison, the earlier runtime revision at core
`e079a65cbae5377431b7702cddf415e4b92f3510` passed 198 Linux-container tests with
one Hyprland skip. That count is historical, not a full Linux-suite pass for
the current revision.

Coverage includes rejected/tampered/replayed root receipts, exact operation
binding, root/user audit outages, durable receipt recovery and deduplication,
malformed/oversized requests, socket deadlines, approval reply loss on both
sides of execution, duplicate taps, defer, replay ordering, stalled connections,
bounded reconnects, and test-harness rejection of missing/wrong-machine evidence.
Isolation coverage additionally includes hidden synthetic secrets, workspace
symlinks and `/proc` routes, environment/descriptor leakage, internet and Unix
socket denial, immutable runtime files, missing enforcement, and cleanup of
detached children after timeout or cancellation. Recovery tests check matched
kernel artifacts, mount identities, persistent helper state and rejection of
unsafe or tampered checkpoints.

To repeat:

```sh
tools/dev-env.sh
.venv/bin/pytest -q
tools/test-linux.sh
# Or select an existing Docker context:
EXO_OS_DOCKER_CONTEXT=your-context tools/test-linux.sh
```

For an x86-64 guest on a host without KVM, QEMU can use TCG explicitly. The
acceptance scripts keep a 180-second SSH deadline by default; set a larger
positive value up to 900 seconds when emulation is slow. Firmware paths can be
overridden for hosts whose OVMF files use different names.

```sh
EXO_OS_VM_ACCEL=tcg EXO_OS_VM_CPU=max EXO_OS_VM_SMP=4 \
  EXO_OS_VM_BOOT_TIMEOUT=600 tools/accept-phase1.sh
```

On a Linux build host with a native mkosi executable, `EXO_OS_MKOSI` selects
that executable and avoids the Arch user-prefix Python path. For example, a
Debian sid tools tree can provide native `systemd-repart` while the image
target remains Arch x86-64:

```sh
EXO_OS_MKOSI=/path/to/mkosi tools/build-image.sh \
  --tools-tree=yes --tools-tree-distribution=debian --tools-tree-release=sid
```

## Still required

Build a new x86-64 `vmtest` image and run `tools/accept-phase1.sh`,
`tools/accept-phase2.sh`, `tools/accept-phase3.sh`, `tools/accept-isolation.sh`
and `tools/accept-ledger.sh` plus `tools/accept-recovery.sh`. Those scripts were
strengthened here; their historical results do not transfer to this revision.
Image boot, systemd socket activation, pacman/snapper on btrfs, current Hyprland,
Orca, real-model chat, and hardware/soak behavior were not exercised in this run.

Kernel tests establish the stated tool-process boundary on the tested Linux
runner, not consent enforcement inside sysd or separation of all desktop
processes. The credentialed-action service passes synthetic local TLS tests;
no live API or credential provisioning was exercised. General response adapters
and independent witness delivery/deployment remain integration work. The protected
OS signer is implemented and source-tested; its fresh image has not yet completed
the runtime acceptance suite.
See [CORE-CONTRACT.md](CORE-CONTRACT.md).
No working machine was migrated. Shared core changes are developed separately
in [Exo PR 70](https://github.com/aska-digital/exo/pull/70); another development
checkout is not modified by these checks.

## Diagnostic image evidence

A separate image at OS `0b189c1`, core `6dbb4d0`, booted under QEMU x86-64 TCG
on the ARM Linux build host. Its provenance and root-owned runtime mode audit
passed. Updated host-side isolation probes reported **11 passed, 0 failed**:
daemon startup, required executor, and eight kernel checks including actual
started-process timeout/cancellation cleanup. Reports are retained under
`image/out/acceptance-0b189c1-6db/isolation/`. That image predates the protected
ledger and subsequent core approval fixes; it is diagnostic evidence only.

Ubuntu's plain `OVMF_CODE_4M.fd` did not expose a SecureBoot variable, so recovery
correctly refused an unknown state. With `OVMF_CODE_4M.secboot.fd` and fresh
unenrolled `OVMF_VARS_4M.fd`, the guest reported SecureBoot=0, SetupMode=1.
No guest firmware variable was fabricated or bypassed. Recovery still refuses
enabled or unavailable Secure Boot state. A first-boot keyring lock-file race in
the mode audit was resolved by waiting for systemd readiness before auditing;
the audit does not ignore disappearing or unsafe paths.

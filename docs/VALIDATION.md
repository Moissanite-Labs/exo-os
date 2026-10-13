# Runtime and isolation validation

Checks on 2026-10-13. The staged witness-publisher candidate pins Exo
`cff62d1e1afbc861e410659f1f26f01afd1ed249`, as recorded in `exo.pin`.
OS `67fe0c7` pins the protected spending policy; `8b02677` adds the VM-only boot
timing fix below; `a3cdf74` adds the portable Linux spend-test fixture. The
candidate pin includes the model-spend boundary, required executor/gate binding,
CoreRuntime/application composition split, and the witness transport implementation.
The publisher is disabled by default and requires explicit administrator action.
Results below name the source and dependency they exercised. Host tests do not
establish Linux enforcement, a booted candidate image, an independent witness
endpoint, or a successful recovery reboot. The earlier `vmtest` image at OS
`eb50e7c` and core `cb36a4a6d19a5192d8fe92b600efcef8f69062d5` booted and
passed 12 ledger checks, then reached two model-spend checks before acceptance
stopped on missing installed required-plugin packaging. That run was archived,
not a seven-suite pass, and excludes this witness candidate. The staged plugin
packaging fix has host coverage below; a new image and its acceptance remain pending.

| Check | Result | Boundary of evidence |
|---|---|---|
| Current witness and required-plugin packaging candidate host suite, macOS arm64, Python 3.13, `.venv/bin/python -m pytest -q` | 579 passed, 20 skipped | OS `2a14e31aaaeb3289a75315b85bd820ffd5fc3d99` with this pending pin update, clean isolated core `cff62d1e1afbc861e410659f1f26f01afd1ed249`; includes the installed-layout `application.create()` case and witness regressions. Root ownership is simulated; Linux witness enforcement, new-image boot/acceptance and an independent endpoint remain unverified |
| Earlier witness and required-plugin packaging candidate host suite, macOS arm64, Python 3.13, `.venv/bin/python -m pytest -q` | 579 passed, 20 skipped | OS `085db2e80624bb2a490069894cf35a646de7495c`, core `2a2b663919af4dfaa1faa79f28c570fa5bf0808d`; includes production plugin-folder staging and a real `application.create()` with the required executor and plugins. Root ownership is simulated; Linux witness enforcement, new-image boot/acceptance and an independent endpoint remain unverified |
| Earlier witness candidate host suite, macOS arm64, Python 3.13, `.venv/bin/python -m pytest -q` | 576 passed, 20 skipped | OS `b03b79e4802d4f0d77a5b00c39af1b45b164220d` with a then-pending pin update to clean isolated core `2a2b663919af4dfaa1faa79f28c570fa5bf0808d`; includes witness alias-separation and malformed-response regressions. Root ownership is simulated; Linux enforcement, image delivery/boot and an independent endpoint remain unverified |
| Earlier staged witness candidate host suite, macOS arm64, `.venv/bin/pytest -q` | 525 passed, 20 skipped | Staged source tree `a9401a6c7061ca44336fa451d5e911d94e580c15`, based on OS `e9cec25`, core `b060974a514cf08c0765119e2e8d952682102a0c`; includes 53 witness host cases and a real loopback mTLS protocol check. Root-ownership behavior is simulated. Three explicit Linux tests were not run; this does not establish Linux ownership/enforcement, image delivery or boot, or an independent endpoint |
| Previous combined Linux arm64 suite, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 484 passed, 10 skipped | OS `25313dfc2ef1e6c09f46c1c0a1569c70ca23d2c2`, core `cb36a4a6d19a5192d8fe92b600efcef8f69062d5`; includes private Broadway GTK and actual model-spend child UID/GID checks. Hyprland and nine namespace checks were skipped under the unchanged container policy; actual Wayland and deployment-kernel acceptance remain pending |
| macOS arm64, Python 3.14, `.venv/bin/pytest -q` | 465 passed, 17 skipped | OS working tree `3f40ffe` plus updated `exo.pin`, core `cb36a4a`; all host tests pass against the exact published core dependency, including the CoreRuntime compatibility aliases; GTK, Hyprland and actual Linux kernel checks are unavailable on this host |
| macOS arm64, Python 3.14, `.venv/bin/pytest -q` | 464 passed, 16 skipped | OS `67fe0c7`, core `11008a8`; GTK, Hyprland and actual Linux kernel checks are unavailable on this host |
| Accessibility stack Linux arm64 main suite, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 482 passed, 10 skipped | OS `2b55be925279443163df6f30c022fef0ae00de3b`, core `11008a8bdf6a3c8dee2900434ca0a57054300bc8`; includes a passing private Broadway GTK smoke for focus transitions, bounded reading view, approval details and simulated held-key events. This historical run does not establish AT-SPI, Orca or Wayland behavior, and was not against the new core pin |
| Earlier Linux arm64 main suite, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 477 passed, 10 skipped | OS `a3cdf745cc52089c004a05862f601519b86b3a21`, core `11008a8bdf6a3c8dee2900434ca0a57054300bc8`; includes 21 model-spend tests, with an actual child UID/GID drop and `0600` file-ownership check in the disposable root container; this is not a run against the new pin |
| Earlier Linux namespace rerun, unprivileged UID 65534, `tests/test_sandbox_linux.py` | 1 passed, 8 environment-blocked | OS `a3cdf745cc52089c004a05862f601519b86b3a21`, core `11008a8bdf6a3c8dee2900434ca0a57054300bc8`; the unchanged Colima policy has `kernel.apparmor_restrict_unprivileged_userns=1`, so bubblewrap cannot write the nested UID map or configure loopback; no system setting was changed |
| Linux arm64, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 407 passed, 10 skipped | OS `2866fcb`, core `b3149a9`; same installed payload as `8d79fdd`, with corrected test inputs; includes actual inherited-listener and signer service identity checks |
| Historical Linux namespace run, Ubuntu kernel 6.8, unprivileged UID 65534, `tests/test_sandbox_linux.py` | 9 passed | Earlier OS `5773dc4` and core `a915e62`; actual bubblewrap, Landlock and seccomp in a disposable privileged container with no host mounts |
| Shared core authority, witness and read-only verification | 82 passed | Core `b3149a9`; durable commits, replay, independent receipt validation, canonical CLI corruption checks and concurrent verification |
| Full core suite and plugins, macOS arm64, Python 3.13 | 1,434 core tests and 749 plugin tests passed | Core `b3149a9`; 65 core skips and one explicitly deselected external legacy-relay integration; all six plugins also passed separately |
| Current core suite and six plugin suites, macOS arm64, Python 3.13 | 1,738 core tests passed, 65 skipped; 753 plugin tests passed | Clean isolated core `cff62d1e1afbc861e410659f1f26f01afd1ed249`, with core and TUI imports verified from that checkout; no deselection. Each plugin ran in its own pytest process. The core budget measures 15,992 lines against the unchanged 16,000 ceiling; these are source tests, not independent review or Linux/image acceptance |
| Earlier core suite and six plugin suites, macOS arm64, Python 3.13 | 1,691 core tests passed, 65 skipped; 753 plugin tests passed | Clean isolated core `2a2b663919af4dfaa1faa79f28c570fa5bf0808d`; no deselection. Each plugin was run in its own pytest process because their local `conftest` imports collide when collected together. The core budget measured 15,989 lines against the unchanged 16,000 ceiling; these are historical source tests, not Linux/image acceptance |
| Earlier core suite and plugins, macOS arm64, Python 3.13 | 1,611 core tests and 753 plugin tests passed | Core `cb36a4a`; 65 core skips, no deselection; the core budget test measures 15,989 lines against a 16,000 ceiling; these are source tests, not independent security review or image acceptance |
| Earlier core suite and plugins, macOS arm64, Python 3.13 | 1,581 core tests and 752 plugin tests passed | Core `11008a8`; 65 core skips, no deselection; all six plugins passed separately and together; the earlier budget test measured 15,982 lines |
| systemd unit dependency graph | passed | Provision/socket/service/firstboot graph checked in Linux with unavailable image executable paths replaced only in temporary validation copies; actual first boot remains necessary |
| Current Ruff `--select F`, `src tests` | passed | OS `2a14e31`, Python static checks |
| Current `bash -n tools/*.sh` | passed | OS `2a14e31`, shell syntax, not a VM acceptance run |
| Current `git diff --check` | passed | OS `2a14e31` plus this pin and documentation update; whitespace/conflict-marker check |

The full core run used an isolated checkout and a closed local bus address. The
earlier `b3149a9` legacy-relay test was deselected because it autodetects an unrelated local
checkout. The inherited `NO_COLOR=1` setting initially caused four TUI tests to
fail by selecting the accessible plain theme. All 31 tests in that UI file and
then the full suite passed with `NO_COLOR` and `EXO_REDUCED_MOTION` unset for the
test process. No application setting was changed. GitHub Actions jobs for core
PR 80 did not start because of an account billing restriction; these are local
results, not a CI pass.

The exact-pin OS run initially exposed a connection-test race: the test sent a
turn on the session callback before the connection finished its pending-approval
snapshot. A controlled delay reproduced `not_connected` before the connected
callback. The test now waits for both readiness and a session; all 21 connection
tests and the full OS suite passed. Runtime connection behavior was unchanged.
The pinned core now contains the required-executor argument-binding and gate-loading
follow-up (`52b4418`) and passes its source tests. Independent security review of
that follow-up and current-image boot/acceptance are still pending; these host
counts do not establish OS activation.

The combined Linux log is retained at
`image/out/validation-25313df-cb36a4a/linux.log`, SHA256
`c7ff3956f97e0c2aa538a3aa1f1f8a3ee5f70d058fe1ed0776cf07fe2f285408`.
The tested OS tree is `bfbafa5ee5d2d6c7e263fb22fb8b98987c5e15fb`.
This run includes the shared surface probe and the new required-executor pin;
its private display does not establish screen-reader or real-compositor behavior.

The Linux runner creates a disposable container with two CPUs and 2 GiB of RAM.
It copies an explicit source list and archives the pinned core commit, installs
the two packages, and destroys the container on exit. It mounts no host folder,
credential directory, socket or desktop. GTK widgets use a private Broadway
display. An explicit container-only test runs an unprivileged client against a
root helper with actual Linux kernel peer credentials and the default
root-owned public-key path. macOS tests simulate those Linux credentials.
The earlier Linux run at core `11008a8` also exercised the model-spend child process without
mocking its identity transition: the child runs as the unprivileged account and
creates a file owned by that UID and GID with mode `0600`. The 21 focused
model-spend tests passed before the full main-suite result above.

The historical nine-pass isolation run used no supplementary groups. The
dedicated Colima VM's AppArmor restriction on unprivileged user namespaces had
caused bubblewrap to refuse startup; for that historical disposable test only,
the restriction was disabled and then restored, and refusal was checked again
after restoration. In the current run the restored value remained `1`: root
bubblewrap setup succeeded, while UID 65534 could neither write the nested UID
map nor configure loopback. The rerun therefore recorded one pass and eight
environment-blocked checks. No system setting or image policy was changed. The
runner never changes a normal host's kernel policy to obtain a pass. An
Arch-image boot must verify the actual deployment kernel separately.

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
  --profile=vmtest --tools-tree=yes \
  --tools-tree-distribution=debian --tools-tree-release=sid
```

`tools/build-image.sh` defaults to the production profile when no profile is
given. For acceptance, select `vmtest` explicitly with `--profile=vmtest` or
`--profile vmtest`. The installed build manifest records that choice; every
acceptance suite rejects missing or non-`vmtest` profile evidence before QEMU
starts. The wrapper rejects duplicate, abbreviated, or unsupported profile
selections. It also rejects `-C`/`--directory` and `-I`/`--include` overrides,
which could select configuration outside the tracked image tree or add profiles;
the documented tools-tree options still pass through.

## Still required

Build a new x86-64 `vmtest` image and run `tools/accept-phase1.sh`,
`tools/accept-phase2.sh`, the named Phase 3 invocation below, `tools/accept-isolation.sh`,
`tools/accept-ledger.sh`, `tools/accept-recovery.sh`, and
`EXO_OS_VM_ACCEPTANCE=1 tools/accept-model-spend.sh --vm-acceptance --image-source <full-built-OS-commit>`.
All seven suites are required. The spend harness records the committed tool
revision separately from the named image source, revalidates spend state as
root without restarting the boot-order unit, and retains the shared manifest,
runtime digest and disposable-VM identity gates. Those scripts
were strengthened here; their historical results do not transfer to this revision.
Image boot, systemd socket activation, pacman/snapper on btrfs, current Hyprland,
Orca, real-model chat, and hardware/soak behavior were not exercised in this run.

Kernel tests establish the stated tool-process boundary on the tested Linux
runner, not consent enforcement inside sysd or separation of all desktop
processes. The credentialed-action service passes synthetic local TLS tests;
no live API or credential provisioning was exercised. General response adapters
and current-image validation of the staged, disabled-by-default witness publisher
and independent endpoint deployment remain integration work. The protected
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

### TCG device discovery timing

Failed and successful boot journals from the same rebuilt `vmtest` image at OS
`98163c0ab8393fd6af8192b3c93b654a304f65ed`, core `b3149a9`, show different delays
after switch-root. The raw image SHA256 is
`63b94275f5272ef182f75aa906bd60974a83583f6077963848f675a31f259018`.
The raw image is archived on the host at
`image/out/build-98163c/exo-os.raw` with that digest.
Times below are monotonic seconds since kernel startup; entries marked "not
compared" were not included in this timing comparison.

Evidence remains in the build container and has a verified host copy at
`image/out/acceptance-98163c-b314/`. The manifest at
`image/out/build-98163c/acceptance-98163c-b314.sha256`
contains 45 files; all 45 passed the recorded checksum verification in
`acceptance-98163c-b314.verify.log` beside the manifest. Successful timing comes from
`01-ledger-attempt2/offline-journal-full.txt`; failed timing comes from
`02-ledger-attempt3/offline-journal-full.txt`, `offline-journal-device.txt` and
`offline-diagnosis.txt`, with `ledger-retry-screen.ppm` in the same failed-attempt
directory. These diagnostics are archived evidence, not committed artifacts.

| Journal event | Failed boot (s) | Successful boot (s) |
|---|---|---|
| Kernel exposes `ttyS0` | 2.598 | not compared |
| Kernel exposes virtio `vda` | 2.841 | not compared |
| Initrd discovers `gpt-auto` | 51.548 | not compared |
| Device waits after switch-root | 102.434 | 73.474 |
| `systemd-tmpfiles-setup-dev-early` starts | 105.432 | not compared |
| `systemd-tmpfiles-setup-dev-early` finishes | 204.271 | 128.020 |
| Device jobs time out | 192.610 to 192.735 | not observed |
| Main-system `udevd` starts / becomes ready | 209.196 / 213.294 | 131.573 |
| Main-system `gpt-auto` discovery | not observed after timeout | 135.262 |

The failed boot exposed kernel devices early, but its root-filesystem device
jobs expired before `udevd` started. The successful boot discovered the root
device after shorter userspace delays. These journals support a TCG device-wait
timing failure; they do not establish that a longer wait fixes every boot.

The `vmtest` profile now appends
`systemd.default_device_timeout_sec=300s systemd.show_status=yes loglevel=6`
to give emulated device discovery a bounded wait and retain useful boot status.
The device-timeout option has been available since systemd v254 and overrides
the default device timeout at boot, as documented in the
[primary systemd kernel-command-line reference](https://github.com/systemd/systemd/blob/main/man/kernel-command-line.xml).
Production retains `quiet loglevel=3` with no device-timeout override. This
profile change has source checks only until a newly built image is booted; the
host acceptance deadline remains separately bounded at 900 seconds.

### VM surface renderer scope

The `vmtest` profile declares `GSK_RENDERER=cairo` in a drop-in for
`exo-surface.service`. The production profile has no renderer override, and the
VM setting is not a session-wide GTK or Chromium setting. GTK documents Cairo as
an incomplete comparison renderer, including unsupported 3D transforms, so this
setting is limited to the disposable software-rendering VM.
See [GTK's Cairo renderer](https://docs.gtk.org/gsk4/ctor.CairoRenderer.new.html)
and [renderer configuration](https://docs.gtk.org/gtk4/running.html#GSK_RENDERER).

Phase 3 checks the service after its deliberate restart. It records the declared
renderer and the main process's effective renderer value, then propagates the
validated value to its synthetic GTK fixture. The AT-SPI diagnostic does the
same before stopping the installed surface. Both evidence contracts require
actual realized `GskCairoRenderer` instances in addition to the environment value.
The full environment is never saved in these renderer records.

These source checks do not establish new-image acceptance. Fresh acceptance and
visual review of the prompt, approval details/buttons, drawer and RTL remain
required. A pass under Cairo establishes software-renderer VM behavior only;
production GPU, hardware and low-power claims remain open.

### Phase 3 image and harness evidence

Set `IMAGE_SOURCE` to the full commit recorded in the selected image's build
manifest, then run from the clean committed harness checkout:

```sh
EXO_OS_VM_ACCEPTANCE=1 tools/accept-phase3.sh \
  --vm-acceptance --image-source "$IMAGE_SOURCE"
```

Phase 3 records the image source and separate harness source. The disposable VM
UUID, installed manifest, pinned core and runtime/image digest checks still run.
A tools-only harness revision can inspect that named image when its installed
source digest matches; it does not change or relabel the image.

App checks acquire a valid baseline before typing and wait up to 60 monotonic
seconds for a valid exact `foot` client-count increase. Each local IPC command is
bounded by the remaining deadline and a five-second command limit. Output has a
streaming byte cap; timeout or excess output stops and reaps the local process
group. Invalid JSON or failed IPC remains invalid evidence, never a zero count.
Baseline and readiness JSON preserve status, output and observation times for
both normal and offline launches. These times describe IPC observations;
application startup time remains unmeasured.

The native held-F6 check records calls to the existing key handler without
changing its decision. A pass requires a delivered repeat while B is pending and
unreviewed, on the initial nonzero physical key code, already held before the
call, without blocking modifiers, and rejected by the original method. A's sole
allow response, no B response, the full `yesterday` text and observed F6 release
are also required. Missing repeat delivery fails coverage. Private Broadway
reports its separate synthetic check. Fresh-image native acceptance remains
required; the earlier failed runs remain failed.

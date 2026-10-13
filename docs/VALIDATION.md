# Runtime and isolation validation

Checks recorded on 2026-10-13. The `7170b3d` `vmtest` image built successfully
and passed a read-only image audit. Fresh VM acceptance is incomplete: phase 3
passed seven checks and failed one. Its core pin
is unchanged from the older `5a86e3b` image, but that image's passing suites do
not transfer to this build.

## Current image evidence

| Identity | SHA256 or commit |
|---|---|
| Built OS | `7170b3d96ec20a335d16a4f683904b9de47f3937` |
| Pinned core | `cff62d1e1afbc861e410659f1f26f01afd1ed249` |
| Raw image SHA256 | `8fd413b1a105e500512ba3a8089f9df28043d6bf9fd5db58dc2f7fad140e156f` |
| Runtime/image source digest | `d5eeb7d65f3c3a77f4339ccf61bad1e0fea9883d9ad57ea55dfe4aba9dce30d9` |
| Committed acceptance harness | `b5e92912e221fa167312839c073d42efa7da48be` |

Build exit status is `0`. The read-only audit reports `PASS`, including modes on
1,657 protected paths, the embedded core pin and build manifest. Its service-only
Cairo override is confined to `vmtest`. Production rendering is unchanged.
Evidence is retained under `image/out/build-7170b3d/build-provenance/` and
`image/out/build-7170b3d/audit-7170b3d-cff62d1/`. These establish the built artifact,
not successful completion of desktop, isolation, spending, recovery or witness acceptance.

| Fresh-image check | Status |
|---|---|
| Phase 3, harness `b5e9291` | Failed: 7 passed, 1 failed, harness exit `1` |
| Ledger, zero-paid spending, recovery, phases 1–2 and isolation | Pending |
| Native AT-SPI accessibility and four witness kernel cases and one installed-flow case | Pending |
| Witness receipts through normal and older-root reboot | Pending |
| Loaded browser pages, memory and Urdu pixels | Pending |

### Fresh phase-3 attempt

The report dated `2026-10-13T14:11:56Z` names the `7170b3d` image and separate
`b5e9291` harness. Its successful checks cover provenance, fake-only model routes,
palette app launch, a question answer, explicit F6 review followed by Y for the
exact pending request, a drawer panel and app launch with exod stopped. The
installed approval flow resolved allow and installed `htop`.

The GTK probe failed at line 147 after five seconds waiting for a visible
unsolicited drawer with the bar active and entry focus retained. The attempt did
not capture actual failing visibility/focus state, so its cause remains unknown.
The probe stopped before the native held-F6 test. The installed F6/Y result does
not establish all approval adversarial checks or that unreached repeat coverage.

Retained screenshots show readable palette text, the pending approval card,
review details/buttons and the answer. Normal and offline launch each recorded
valid exact `foot` window counts from 0 to 1. A timed-out intermediate IPC
observation remains invalid evidence. These observations do not measure actual
app startup latency. Surface PSS was 60 MiB, labelled MB by the script. No
memory-budget conclusion follows.

The failed report and logs remain in
`image/out/build-7170b3d/acceptance-7170b3d-cff62d1/06-phase3-attempt1-b5e9291/`.
Report `accept-phase3.txt` SHA256:
`332bb956ecd10b6770f24059f27b1592ea04e95f04583177ba1ceb9545a0eec7`.
Any further state diagnostic is separate evidence and does not replace this run.

### Later focus diagnostics on the same raw image

These tools-only diagnostics used the unchanged `7170b3d` raw image and core
`cff62d1`. They neither installed a runtime repair nor passed phase-3 acceptance.
The original seven-pass, one-fail report remains the acceptance result.

Attempt 08 observed a local GTK focus target while `Gtk.Window.is_active()` was
false. Attempt 10 delivered the full `exo focus marker` through actual keyboard
input with that GTK flag false. The flag is informational. It cannot establish
keyboard delivery on its own. Those observations did not test typing across a
panel update.

Attempt 14 used the shared version-2 observer against the old installed runtime.
It failed actual typing during an unsolicited panel update. Attempt 16 added
input-state observation to that stage. All 16 physical keys arrived. The panel
callback changed the selection from empty to `[0, 4]` over the `exo ` prefix.
The next key, `f`, replaced that selection. The final entry and model text were
`focus marker`, rather than `exo focus marker`. This identifies text selection
loss in that diagnostic. It does not retrospectively identify the original
attempt's unrecorded state.

The trace is retained at
`16-panel-update-diagnostic-attempt1-7d7f559/evidence/out/panel-update-input-trace.json`
under the current acceptance directory. No runtime or acceptance-probe repair
was installed for that diagnostic. Full surface restoration was not confirmed
within 30 seconds. QEMU was then stopped. The raw backing image was unchanged.
Neither this shutdown nor the diagnostic establishes successful restoration.

### Source repairs awaiting a fresh build

The selected source repairs cover the test budget (PR 84), spending across a
fork and crash (85), decoded writer keys (86), witness publication checks (89),
build receipts (90), complete host generations (92), and focus lifecycle and
version-2 observation (94). Their inclusion in a local composition is source
work, not evidence that they run in the `7170b3d` image.

The local combined candidate is `adce2fa`, which adds only the `e41f22c`
documentation merge to `da7588a`. Selected repair heads are in its ancestry.
No GitHub merge or new native image build is claimed. The macOS focus candidate
at runtime `a048a58` with the later documentation-only `e41f22c` recorded 754 passing tests,
21 skipped and five timing failures in 455.55 seconds.
Independent full Linux ARM host validation of the frozen source candidate
remains pending. Native witness acceptance requires four kernel cases and one
installed-flow case, five cases total. All five remain pending on the fresh image.

The core pin remains `cff62d1`. OS work tracked in issues 16 and 18 awaits the
stable shared-core authority/cursor contract tracked in core issue 109.
Credential issue 71 and sandbox issue 22 require decisions. This record assumes
no ruling on either issue.

The publisher is disabled by default and requires explicit administrator action.
No endpoint under independent administration is deployed. Real paid-model chat
and a deliberate paid cap test need a test key, prices and limits. Orca speech,
voice/phone clients, accessible keycaps, onboarding, spare-hardware trial,
phone authentication, weeks of soak and a working-machine trial remain
unverified. Build or host results do not close those criteria.

## Historical image `5a86e3b`

These reports were produced on 2026-10-13 in x86-64 QEMU/TCG `vmtest` guests.
They apply only to the identified older artifact. Counts include each suite's
provenance check. The five successful suites total 47 passing checks.

| Identity | SHA256 or commit |
|---|---|
| Built OS | `5a86e3ba39d359e4e747d30cd0f9c50050775c22` |
| Pinned core | `cff62d1e1afbc861e410659f1f26f01afd1ed249` |
| Raw image SHA256 | `8564b1e5c2cba0a79e3adba996304139247a9060f76941f6d6e5e028dac83ee1` |
| Runtime/image source digest | `18929bcb026b4a209755984998c0cf35b32e8c8d8183e12f9163d0c3892795c8` |

| Suite | Report result | Evidence and limit |
|---|---|---|
| Protected ledger | 12 passed, 0 failed | Installed signer, projection verification, tamper and outage checks. No off-machine witness |
| Model spend | 12 passed, 0 failed | Tools-only harness `e26775fefdaecf93936759e31dd52cc930edcdf8`. Offline fake turns, mocked HTTP refusals, persistent UUID/stop and readable transcripts. No paid request |
| Recovery | 10 passed, 0 failed | Actual older-root reboot. Current home/store, signer, helper, stopped spending journal and synthetic egress reservation retained. Original root preserved |
| Phase 1 | 6 passed, 0 failed | Wayland desktop, daemon restart, offline chat, portable signed prefix through sequence 7 and live profile change. Three Chromium windows were visually blank |
| Phase 2 | 7 passed, 0 failed | 20 applied changes, 19 commits with diffs, 39 added ledger rows. Automatic revert, profile/package undo, root refusals and no boot tools. Signed prefix through sequence 49 |
| Phase 3 | 3 passed, 5 failed. Harness failed | App launch, approval, drawer, offline launch and GTK checks failed |
| Isolation | 12 passed, 1 failed. Harness failed | Spending-state oracle assumed Bash `is_error` proved access. Raw outcome missing, so neither spending-state denial nor disclosure is confirmed |

Reports remain under `image/out/build-5a86e3b/acceptance-5a86e3b-cff62d1/`.
Only the result summary and artifact identifiers are recorded here. Raw logs,
VM overlays, firmware and private state are not published.

| Relative report | SHA256 |
|---|---|
| `01-ledger-attempt1/accept-ledger.txt` | `a7747ad2f7ef4f9311330ba4af4c8bafad106f28a2f79fe4daee0f2070560d12` |
| `02-model-spend-attempt2-e267/accept-model-spend.txt` | `b0c9440a701e7545430787eded33da9dd66f85a3e786e8ae12776ad07a0cc707` |
| `03-recovery-attempt2/accept-recovery.txt` | `be4cb74a5b2daf05312811440675aafe5e038bcd482bb2ee63e001901e5cca18` |
| `04-phase1-attempt1/accept-phase1.txt` | `cc91704340d2c0b1c962b2c70aa6a8bff3186ba4e027e18b4b79d59ac21ea0f4` |
| `05-phase2-attempt1/accept-phase2.txt` | `38366fa24db9c87a8e18bbf44baaae151c5a5868becc8c4f4fe303ac44a56e8a` |
| `06-phase3-attempt1/accept-phase3.txt` | `5e20cb0d60fe0e2b3366c2d1ab06e4ded10705681abb0375d797e028b80a3010` |
| `07-isolation-attempt1/accept-isolation.txt` | `37edadab0d83a78f3baef467421d15204f027f502d9648e15128f62fbe2bc677` |

The first spending attempt exited `1`: its probe omitted the required protected
policy environment, and a boot-order service restart disrupted the desktop.
Report SHA256: `b761d1bf78fbbbc11f4655c1e1774fe2ef2001ffba1a9749f7937b78b1116ae4`.
Its corrected tools-only retry retained the installed image identity and digest.

The first recovery attempt exited `255` after host OOM killed QEMU at a forced
3072 MiB guest allocation. Report SHA256:
`23f74c1cd361ce02265d9692e369231e70db81f39d591f79f9149c47e2d5908c`.
The successful retry used recovery's declared 2048 MiB default. Both failed
attempts remain retained and are excluded from the successful-suite count.

The old phase-3 diagnosis recorded missing surface glyphs and a delayed allow.
Source review found that ordinary Y/N typing could enter approval. The retained
VM logs do not establish the exact keystroke that caused the delayed allow.

The phase-1 Chromium figures are launch-only measurements in
[MEASUREMENTS.md](MEASUREMENTS.md). The phase-3 approval fix is included in the
new image. The corrected isolation oracle is in the committed harness. Neither
fix changes the status of its historical failed run.

### Renderer diagnostic on the older image

Attempt 4 compared baseline, a temporary service-only Cairo override and restored
baseline on the same `5a86e3b` image. At 20 seconds the baseline and restored
prompt screenshots were visually blank and byte-identical, SHA256
`f9e4422c74f48d63866cb3e42263baba355a1b4877d1d036e6d6950f2314a9f4`.
Cairo showed readable placeholder and palette text, including `Termi`,
`Open Terminal` and `Ask Exo: Termi`.

All three arms observed exact `foot` counts from 0 to 1 through recorded IPC
queries. This diagnostic does not measure actual launch latency. It justified
the `vmtest` service override, not a production-renderer or hardware claim.
Fresh-image prompt, approval details/buttons, drawer, browser and Urdu visual
review remain required.

| Artifact in `08-rendering-diagnostic-attempt4/` | SHA256 |
|---|---|
| `diagnose-rendering.txt` | `eac18c33311cf85e1613e1156cc185c476069ec494deac58012759e69fa352f2` |
| `files-portable.sha256` | `38d97c0a36de11b16f56d49f437e805126b75b8969e84529794250870d638db9` |

## Host checks and older image evidence

Host tests establish behavior in their stated source/environment. They do not
establish deployment-kernel enforcement, a booted new image or an independent
endpoint. The earlier `eb50e7c`/core `cb36a4a` image passed twelve ledger checks,
then stopped after two spending checks on missing required-plugin packaging.
That archived attempt is not a seven-suite pass.

| Check | Result | Boundary of evidence |
|---|---|---|
| Reviewed tools-only readiness draft, macOS arm64, Python 3.13, `python -m pytest -q` | 707 passed, 21 skipped | Draft based on `7170b3d`, core `cff62d1`. Now committed as harness `b5e9291`. Recorded checks cover Python static analysis/compile, shell syntax and whitespace. Runtime/image digest unchanged. Fresh native VM checks remain pending |
| Earlier witness and required-plugin packaging candidate host suite, macOS arm64, Python 3.13, `.venv/bin/python -m pytest -q` | 579 passed, 20 skipped | OS `2a14e31aaaeb3289a75315b85bd820ffd5fc3d99` with the then-pending pin update, clean isolated core `cff62d1e1afbc861e410659f1f26f01afd1ed249`. Includes the installed-layout `application.create()` case and witness regressions. Root ownership is simulated. Linux witness enforcement, new-image boot/acceptance and an independent endpoint remain unverified |
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
| Earlier pinned-core suite and six plugin suites, macOS arm64, Python 3.13 | 1,738 core tests passed, 65 skipped. 753 plugin tests passed | Clean isolated core `cff62d1e1afbc861e410659f1f26f01afd1ed249`, with core and TUI imports verified from that checkout. No deselection. Each plugin ran in its own pytest process. The core budget measures 15,992 lines against the unchanged 16,000 ceiling. These are source tests, not independent review or Linux/image acceptance |
| Earlier core suite and six plugin suites, macOS arm64, Python 3.13 | 1,691 core tests passed, 65 skipped; 753 plugin tests passed | Clean isolated core `2a2b663919af4dfaa1faa79f28c570fa5bf0808d`; no deselection. Each plugin was run in its own pytest process because their local `conftest` imports collide when collected together. The core budget measured 15,989 lines against the unchanged 16,000 ceiling; these are historical source tests, not Linux/image acceptance |
| Earlier core suite and plugins, macOS arm64, Python 3.13 | 1,611 core tests and 753 plugin tests passed | Core `cb36a4a`; 65 core skips, no deselection; the core budget test measures 15,989 lines against a 16,000 ceiling; these are source tests, not independent security review or image acceptance |
| Earlier core suite and plugins, macOS arm64, Python 3.13 | 1,581 core tests and 752 plugin tests passed | Core `11008a8`; 65 core skips, no deselection; all six plugins passed separately and together; the earlier budget test measured 15,982 lines |
| systemd unit dependency graph | passed | Provision/socket/service/firstboot graph checked in Linux with unavailable image executable paths replaced only in temporary validation copies; actual first boot remains necessary |
| Earlier Ruff `--select F`, `src tests` | passed | OS `2a14e31`, Python static checks |
| Earlier `bash -n tools/*.sh` | passed | OS `2a14e31`, shell syntax, not a VM acceptance run |
| Earlier `git diff --check` | passed | OS `2a14e31` plus the then-pending pin and documentation update. Whitespace/conflict-marker check |

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
that follow-up remains separate from image acceptance. These host counts alone
do not establish OS activation.

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
after restoration. In the recorded namespace rerun the restored value remained `1`: root
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

`tools/build-image.sh` defaults to production. Acceptance requires an explicit
`--profile=vmtest` or `--profile vmtest`. The only other wrapper options are
`--tools-tree`, `--tools-tree-distribution`, `--tools-tree-release`, and
`--tools-tree-profile`. Each accepts `--option=value` or `--option value`.
Unknown options, abbreviations, grouped short flags, duplicate profiles and
configuration/include overrides are rejected.

The wrapper refuses `image/mkosi.local.conf` and `image/mkosi.local`, including
dangling symlinks. Those automatic mkosi overrides could change the profile or
include files outside the checked source tree.

### Successful image publication

Each admitted attempt acquires `image/out/.build-lock`, invalidates the current
success receipt, and stages raw output and the extra tree under a fresh
`image/out/.build-attempt.*` directory. A failed build keeps that attempt and the
previous raw image and extra tree. After mkosi exits zero, publication moves the
previous pair into `image/out/build-history/` and publishes the new pair.
The schema 1 `exo-os-build-success.json` receipt is published last.
Its file is flushed before the atomic rename, then the output directory is
flushed. A flush failure removes acceptance eligibility. These flushes do not
establish that the whole build survives a host power loss.

The receipt records SHA-256 hashes of the raw image and staging manifest, plus
the manifest's OS commit, source digest, profile and core pin. Acceptance holds
the same lock and checks this pair against the receipt and current source inputs
before QEMU starts. Missing, changed or partly published artifacts fail this
check. The VM UUID and installed-manifest checks still run after boot.

Failed attempts and history need manual retention management. After an interrupted
process leaves a lock directory, check that no build or acceptance process is
running before removing that empty lock. A lock alone never establishes a
successful build.

### Schema 2 source inputs and archive limits

`EXO_OS_BUILD.json` remains schema 2. Its `source_sha256` hashes `pyproject.toml`,
`src/`, the checked image tree, and these builder files:

- `tools/build-image.sh`, `tools/build-provenance.py`, `tools/build-profile.py`
- `tools/env.sh`, `tools/source_identity.py`, `tools/build-receipt.py`

Generated `image/out/`, `image/mkosi.tools/`, `image/mkosi.tools.manifest`,
`image/mkosi.cache/`, `image/mkosi.builddir/`, Python bytecode and `__pycache__`
are excluded. Other acceptance and diagnostic tools are outside this digest,
so their committed revision can be recorded separately from the image revision.
Changing builder bytes requires a fresh image build. Earlier images, including
`7170b3d`, cannot acquire this new digest or receipt retroactively.

OS source identity uses Git only when its physical repository root equals the
source root. It then falls back to `.EXO_OS_SOURCE_ID`, followed by
`EXO_OS_SOURCE_COMMIT`. Every supplied identity must be a lowercase 40-hex value,
and supplied identities must agree. An archive nested inside another Git
checkout keeps its own identity. Symlink paths resolve to the same physical root.
Git builds require a clean committed checkout before an attempt can invalidate
the success receipt. Acceptance also requires a clean committed harness checkout.
Archive marker files must be regular files of at most 64 bytes, with 40 lowercase
hex characters and an optional newline. Symlinks, dangling links and other file
types are rejected.

Git-free OS identity markers and core `.EXO_PIN` files are trusted operator
assertions. Matching `.EXO_PIN` to `exo.pin` does not prove that an Exo archive's
contents came from that commit. Supply the core archive through a trusted process
or use the Git fetch/checkout path. That path requires `.deps/exo` to be its own
clean checkout and confirms that its HEAD equals the pin before wheel building.
Modified tracked files and untracked files cause refusal. Git worktrees with a
`.git` file are supported. Dependency paths outside this source tree are refused.
The receipt binds the resulting raw image and
manifest bytes. It does not authenticate archive assertions, resolved wheels or
the host toolchain. Dependency resolution still includes `textual>=1`.

## Still required

Build the selected source candidate, record its identity and audit it, then run
all seven main suites on that fresh image: phase 1, phase 2,
the named phase-3 invocation below, isolation, ledger, recovery and model spend.
The corrected harness records its revision separately from the built image,
retaining the VM identity, core pin and source-digest gates. Historical passes
do not transfer. Native AT-SPI, the four witness kernel cases and one installed-flow case and
receipt persistence need their separate fresh-image checks.

Real-model chat, loaded-web-app memory, Orca, Urdu visual review and hardware/soak
behavior remain unverified. The historical suites above include actual boot,
Wayland, service activation, package changes and recovery within their limits.

Kernel tests establish the stated tool-process boundary on the tested Linux
runner, not consent enforcement inside sysd or separation of all desktop
processes. The credentialed-action service passes synthetic local TLS tests;
no live API or credential provisioning was exercised. General response adapters
and current-image validation of the staged, disabled-by-default witness publisher
and independent endpoint deployment remain integration work. The protected
OS signer has historical VM ledger/recovery evidence. The new image has not yet
completed runtime acceptance.
See [CORE-CONTRACT.md](CORE-CONTRACT.md).
The OS repository has no hosted workflow run or successful CI claim for these
results. Hosted core checks failed before test steps because of account billing.
Independent review remains separate from local test results. No working machine
was migrated. Shared core changes are developed separately
in [executor binding PR 91](https://github.com/aska-digital/exo/pull/91),
[model spending PR 87](https://github.com/aska-digital/exo/pull/87),
[witness transport PR 88](https://github.com/aska-digital/exo/pull/88) and
[authority-key alias fix PR 95](https://github.com/aska-digital/exo/pull/95),
all included in the `cff62d1` pin. Another development checkout is not modified
by these checks.

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

The shared GTK result is `exo-surface-gtk-v2`. Native prompt checkpoints require
the full `exo focus marker` text in both entry and model, a matching sequence of positive physical
key codes, a mapped local entry target, no response and no keyboard review.
All seven named checkpoints must appear in order. The panel update during marker
typing must preserve its prefix. A suffix alone is a failure.

Prompt `Gtk.Window.is_active()` values are recorded as information. An older
remapped fixture reported false while actual keyboard input reached the entry.
The v2 contract uses received keys and text to prove prompt delivery. Card
activation/focus-loss guards and explicit drawer activation checks remain required.

The separate `external_panel_update` record requires a panel update during actual
`yesterday` typing in another window. Full text, positive physical key codes,
the external active target, unchanged prompt/model text and no responses sent
are mandatory. Broadway reports explicit synthetic coverage for this case and
the prompt checkpoints. Missing evidence, an older v1 result or a nonzero probe
exit status cannot pass the v2 validator.

The native held-F6 check records calls to the existing key handler without
changing its decision. A pass requires a delivered repeat while B is pending and
unreviewed, on the initial nonzero physical key code, already held before the
call, without blocking modifiers, and rejected by the original method. A's sole
allow response, no B response, the full `yesterday` text and observed F6 release
are also required. Missing repeat delivery fails coverage. Private Broadway
reports its separate synthetic check. Fresh-image native acceptance remains
required; the earlier failed runs remain failed.

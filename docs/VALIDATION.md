# Runtime and isolation validation

Current checks on 2026-10-13. Exo dependency:
`d011efae2176121613b500dabfa1382d40d89736`, as recorded in `exo.pin`.
These results cover runtime trust, isolated execution and recovery logic. They
do not establish a newly booted image or a successful recovery reboot.

| Check | Result | Boundary of evidence |
|---|---|---|
| macOS arm64, Python 3.13, `.venv/bin/pytest -q` | 321 passed, 13 skipped | Includes persistent egress recovery checks at OS `b155182`; GTK, Hyprland and actual Linux kernel checks are unavailable on this host |
| Linux arm64, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 321 passed, 10 skipped | OS `8aca057`, before nine additional recovery tests; includes GTK full-payload rendering and actual inherited-listener service identity; skips Hyprland and the separately enabled namespace suite |
| Linux arm64, Ubuntu kernel 6.8, unprivileged UID 65534, `tests/test_sandbox_linux.py` | 9 passed | Actual bubblewrap, Landlock and seccomp; disposable privileged container, no host mounts |
| Shared core protocol and strict shutdown checks | 49 passed; 13 passed with warnings treated as errors | Includes delayed handler registration and unread transport shutdown regressions |
| Ruff `--select F`, `src tests` | passed | Python static checks |
| `bash -n tools/*.sh` | passed | Shell syntax, not a VM acceptance run |
| `git diff --check` | passed | Whitespace/conflict-marker check |

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

## Still required

Build a new x86-64 `vmtest` image and run `tools/accept-phase1.sh`,
`tools/accept-phase2.sh`, `tools/accept-phase3.sh`, `tools/accept-isolation.sh`
and `tools/accept-recovery.sh`. Those scripts were
strengthened here; their historical results do not transfer to this revision.
Image boot, systemd socket activation, pacman/snapper on btrfs, current Hyprland,
Orca, real-model chat, and hardware/soak behavior were not exercised in this run.

Kernel tests establish the stated tool-process boundary on the tested Linux
runner, not consent enforcement inside sysd or separation of all desktop
processes. The credentialed-action service passes synthetic local TLS tests;
no live API or credential provisioning was exercised. General response adapters,
the separate core signer and off-machine audit anchor remain integration work.
See [CORE-CONTRACT.md](CORE-CONTRACT.md).
No working machine was migrated. Shared core changes are developed separately
in [Exo PR 70](https://github.com/aska-digital/exo/pull/70); another development
checkout is not modified by these checks.

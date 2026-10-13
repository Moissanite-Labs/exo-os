# Runtime validation

Run on 2026-10-13 UTC (2026-10-12 America/Chicago). Exo dependency:
`e079a65cbae5377431b7702cddf415e4b92f3510`, exactly as recorded in `exo.pin`.
These results cover the runtime trust/recovery change, not a newly booted image.

| Check | Result | Boundary of evidence |
|---|---|---|
| macOS arm64, Python 3.13, `.venv/bin/pytest -q` | 191 passed, 3 skipped | GTK module, Hyprland, and Linux-only root integration unavailable on this host |
| Linux arm64, Ubuntu 24.04, Python 3.12, `tools/test-linux.sh` | 198 passed, 1 skipped | Hyprland config verification needs the image's compositor |
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

Coverage includes rejected/tampered/replayed root receipts, exact operation
binding, root/user audit outages, durable receipt recovery and deduplication,
malformed/oversized requests, socket deadlines, approval reply loss on both
sides of execution, duplicate taps, defer, replay ordering, stalled connections,
bounded reconnects, and test-harness rejection of missing/wrong-machine evidence.

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
`tools/accept-phase2.sh`, and `tools/accept-phase3.sh`. Those scripts were
strengthened here; their historical results do not transfer to this revision.
Image boot, systemd socket activation, pacman/snapper on btrfs, current Hyprland,
Orca, real-model chat, and hardware/soak behavior were not exercised in this run.

The test suite does not establish phase-4 tool isolation or consent enforcement
inside sysd. The core broker, credential isolation, controlled egress, separate
core signer and off-machine audit anchor remain integration work. See
[CORE-CONTRACT.md](CORE-CONTRACT.md). No working machine was migrated and no
changes were made to the base Exo repository.

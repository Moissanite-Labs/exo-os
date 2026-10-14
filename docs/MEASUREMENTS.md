# Measurements

Numbers measured on Exo OS images, with how they were taken. Guesses in `research/04-small-hardware.md`
are replaced here as real numbers arrive.

## Chromium launch snapshot (2026-10-13)

The older `5a86e3b` image, core `cff62d1`, ran in an x86-64 QEMU/TCG `vmtest`
guest. Its phase-1 report records Chromium PSS 619 MiB, Hyprland 229 MiB,
exod 35 MiB, MemTotal 2859 MiB and MemAvailable 1365 MiB. The script labels
these numbers MB. Its `/proc` conversion divides KiB by 1024.

The retained `accept-apps.png` shows three blank Chromium windows. This measures
launched processes, not loaded web apps or a signed-in workload. The loaded-app
memory criterion remains open. Screenshot SHA256:
`c370c34eb3d197cc5eee31676559d02f3452723fe71f5c6238c42f8b93ad90d0`.
See [historical image evidence](VALIDATION.md#historical-image-5a86e3b).

## Historical surface snapshot (2026-10-13)

The failed phase-3 attempt on `7170b3d`, core `cff62d1`, harness `b5e9291`,
records surface PSS 60 MiB, labelled MB by the script. This is one snapshot in
the QEMU/TCG `vmtest` environment with a service-only Cairo renderer. It does not
establish an agreed memory budget, loaded browser workload or hardware use.
See [the failed attempt and limits](VALIDATION.md#historical-phase-3-attempt).

## Renderer diagnostic (2026-10-13)

A baseline/Cairo/restored-baseline comparison on the same older `5a86e3b` image
showed readable surface text with a service-only Cairo override. All three
arms observed an exact `foot` window count increase from 0 to 1. These bounded
IPC observations do not measure actual app startup latency or hardware speed.
The report and visual findings are in
[VALIDATION.md](VALIDATION.md#renderer-diagnostic-on-the-older-image).

## Memory with Chromium and three web apps (phase 1, check 4)

2026-10-12, image from `phase-1` at 7e0f268 plus the `--no-first-run` Chromium flag, Exo 4e6e644.
QEMU/KVM VM with 2560 MB RAM and 2 vCPUs on charminar, desktop rendered through virgl.
`tools/measure-memory.sh`: WhatsApp, Gmail and Drive opened as Chromium `--app` windows, then 60 s to
load. They were **not signed in**, so they show sign-in pages; signed-in apps use more (not measured).
PSS from `/proc/<pid>/smaps_rollup`, summed per group.

| Group | PSS | Processes |
|---|---|---|
| Chromium (all processes) | 770 MB | 16 |
| Hyprland | 113 MB | 1 |
| exod (Exo core, with exo-gate, exo-memory, exo-context, exo-os) | 65 MB | 2 |
| exo-surface (prompt bar), idle | 50 MB | 2 |
| Xwayland | 29 MB | 1 |
| xdg-desktop-portal(s) | 27 MB | 3 |
| everything else (systemd, NetworkManager, journald, wireplumber, greetd, sshd) | about 60 MB | |
| **Sum of PSS** | **1170 MB** | |

Guest `MemAvailable` was 952 MB of 2453 MB. On the test laptop (5.7 GB) that would leave room for the
1 GB free that research note 04 §9 asks for; this is an inference from the VM number, not measured on
the laptop.

Other numbers from the same day:
- exo-surface after drawing panels and approval cards (phase 3 acceptance): 132 to 137 MB PSS.
- Idle desktop before any app: Hyprland 117 MB, exod 33 MB (phase 1 acceptance, first run).

## Speed

- Package install through exo-sysd (`htop`, refresh plus full upgrade on a fresh image, snap-pac
  snapshots before and after): 5.5 s wall time in the VM.

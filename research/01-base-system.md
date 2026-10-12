# Exo OS research 01: base system options

Date: 2026-10-12. Status: research note, not a decision.
Rules used: every claim has a link. Anything marked (guess) is my reasoning, not a sourced fact.
No benchmarks are given. Where I found no source, I say so.

Context read: D-014 in exo-audit DECISIONS.md (one terminal-like surface, panels summoned by the model) and research/PRINCIPLES.md (the log is the source of truth, ask when uncertain, act when confident). Both are read-only inputs.

## 1. What Exo OS needs from a base

1. Every system change is reversible, or clearly marked as not reversible.
2. `exod` (Python, system service) can propose a change, test it, and roll it back without a human at a shell.
3. The reversibility gate in `exod` can map onto a real OS mechanism. Reversible change: auto-run. Irreversible: ask.
4. Runs well on this machine: 5.7 GB RAM, low-power CPU (from the task brief).
5. Later: a portable device, plus VM and USB images to test and ship.
6. A small team can build it and keep it up to date.

The key design point: the OS gives two kinds of undo. Machine state (packages, system config) and user state (home, data). Most options below cover machine state well and home poorly. Exo needs its own answer for home and for the event log (see section 8).

## 2. Option (a): Arch plus btrfs snapshots (snapper, snap-pac, limine-snapper)

How changes become reversible
- snap-pac takes a pre and a post snapshot around every pacman transaction, using snapper. Source: https://github.com/wesbarnett/snap-pac
- Omarchy snapshots on every update and on demand with `omarchy-snapshot create`, and you boot or restore a snapshot from the Limine menu. Source: https://raw.githubusercontent.com/basecamp/omarchy/master/manual/47-system-snapshots.md
- Limits stated by Omarchy's manual: only the root filesystem is covered, `/home` and `~/.config` are not rolled back, it only works with Limine, and "Direct Boot" setup refuses to run on some firmware (American Megatrends, Apple). Same source.
- Snapper wiki page could not be fetched (bot block), so I rely on the snap-pac README for snapper behaviour.

Agent safety
- `exod` can call `snapper create` before a change, apply it, and test. If the test fails it can run a rollback. (guess: a live rollback of a running root usually needs a reboot, so "safe propose and revert" means a reboot step or a staged boot, not an instant undo.)
- Only changes made through pacman are auto-snapshotted by snap-pac. Direct edits in /etc are not, unless `exod` snapshots first. (guess, follows from the snap-pac description: it hooks pacman transactions.)
- No dry run of a whole system. You find out after applying.

Build effort (small team)
- Lowest of all options. The pieces exist and are known to work together (Omarchy proves it).
- You still own a rolling-release distro: every update can break things, and a snapshot is the only net.

Hardware fit
- Lean. Arch has no build-from-source step for normal use. Btrfs adds some metadata overhead and snapshot disk use (guess: small on a small root, grows with churn).
- Portable device: x86 is well supported. ARM is not official Arch (guess, I did not find a source; Arch ARM is a separate project).

VM and USB image
- archinstall can run from a JSON config for scripted installs. Source: https://github.com/archlinux/archinstall
- Omarchy ships its own ISO. Source: https://akitaonrails.com/en/2025/09/12/omarchy-2-0-install-with-the-omarchy-iso/
- Build repeatable images yourself with archiso or archinstall (guess: archiso is standard but I did not fetch its docs).

Maintenance cost
- Medium to high over time. Rolling updates, manual care for config drift (/etc is mutable), AUR packages if used.

## 3. Option (b): NixOS

How changes become reversible
- The whole system is built from one declarative config. `nixos-rebuild switch` builds, makes it the boot default, and applies it. `test` applies it without making it the boot default, so a reboot restores the old one. `boot` sets it for next boot only. `build` builds without activating. Source: https://nixos.org/manual/nixos/stable/
- Each configuration gets a boot menu entry, so you can roll back from the boot menu. Same source.
- `build-vm` builds a QEMU VM of the configuration. Same source.
- Not covered by this: user data in home, mutable app state. (guess: same gap as Arch, since Nix does not snapshot data.)

Agent safety
- Best fit of all options for "propose, test, revert". The agent edits a config file (text, diffable, in git), runs `build` to check it evaluates and builds without touching the system, runs `test` to apply live with a reboot as the undo, then `switch` to keep it. (guess on the workflow shape; the commands are from the manual above.)
- A text diff is a perfect object for the approval card in D-014. The user sees exactly what changes.
- Risk: the Nix language is a hard target for models and for a non-technical owner. (guess)

Build effort
- Medium to high up front: learn Nix, package `exod` as a Nix package and NixOS module. Then very cheap to reproduce and to ship images. (guess on effort split)
- Omarchy's Hyprland setup would have to be rewritten as Nix modules.

Hardware fit
- The running system is fine on low RAM. Building is the problem: one user config reports a first build peaking near 12 GB RAM, with advice to add swap. Source: https://discourse.nixos.org/t/best-practice-for-running-nixos-on-low-end-system/77592 (via search summary, see the search result for that thread). Evaluation alone can also be the RAM bottleneck, and the thread suggests zram, one core, one job, or a remote builder with `--build-host`.
- A contributor estimated about 700 MiB to evaluate and build a system and about 1.5 GB for a desktop. Source: https://discourse.nixos.org/t/adding-nixos-entry-to-libosinfo-recommended-resources/6441 (estimate from experiments, not official).
- I found no official minimum RAM figure.
- For 5.7 GB: workable if builds come from a cache or another machine. (guess)

VM and USB image
- `build-vm` is built in (source above). Installer and disk images are produced from the same config. (guess: I did not fetch image-building docs.)

Maintenance cost
- Low once built: upgrades are atomic, rollback is cheap. Cost shows up in Nix expertise and in packages missing from nixpkgs.
- `system.autoUpgrade` exists and can reboot when the kernel changes. Source: https://nixos.org/manual/nixos/stable/

## 4. Option (c): image-based (Fedora Atomic, bootc, ostree, Universal Blue)

How changes become reversible
- rpm-ostree rolls back to the previous deployment without touching user data in /var. Source: https://github.com/justinkinney/rpm-ostree/blob/main/docs/index.md (a fork's docs page, via search summary; check upstream).
- You can layer RPMs on top of the base image, but a new package needs a reboot into a new deployment. Source: https://discussion.fedoraproject.org/t/experiences-from-testing-fedora-silverblue/21528
- bootc gives "transactional, in-place operating system updates using OCI/Docker container images". The kernel is inside the image, and the default storage backend is ostree. Source: https://bootc.dev/bootc/
- rpm-ostree development focus is shifting to bootc and dnf. Source: https://github.com/justinkinney/rpm-ostree/blob/main/docs/index.md (via search summary).
- Universal Blue builds images with bootc from Fedora Atomic bases, defines them with a Containerfile, builds with GitHub Actions, signs them with Sigstore, hosts on ghcr.io, and keeps 90 days of archives for rollback. It provides a template repo for custom images. Source: https://universal-blue.org/
- Roadmap note: Fedora Atomic Desktops can already be used as container images, with caveats. Source: https://gitlab.com/fedora/ostree/sig/-/issues/26

Agent safety
- The OS is read-only at runtime. An agent cannot damage the base by accident. A change means "build a new image, boot into it, roll back if bad". (guess on workflow; the mechanism is from the sources above.)
- Fits the gate very cleanly: a runtime action is reversible by default, a base-image change is a staged deployment.
- Weak spot: slow loop. Each system change is an image build (guess: minutes in CI, longer on this machine). Layered packages are quicker but need a reboot.
- Apps live in containers or Flatpak. `exod` itself would be baked into the image as a service.

Build effort
- Medium. A Containerfile plus CI is familiar work. Universal Blue's template reduces it. Hyprland on Fedora Atomic is a custom image job (guess: there is a Hyprland Atomic spin, I did not verify).
- Packaging is RPM, not pacman. Omarchy's Arch-specific scripts do not carry over (see option d).

Hardware fit
- I found no source for minimum RAM on Silverblue. (not verified)
- Image builds are heavy for 5.7 GB, so build in CI and pull images. (guess)
- Portable device: Fedora supports x86 and ARM (guess, not sourced here). bootc suits a fleet or device that pulls signed updates.

VM and USB image
- bootc-image-builder makes disk images. Its sources moved to osbuild/image-builder. Source: https://github.com/osbuild/bootc-image-builder . I could not confirm the exact output formats (qcow2, ISO, raw) from the pages I fetched. (not verified)

Maintenance cost
- Low per machine, medium for the image pipeline. Fedora's cadence is fixed releases, so you rebase on a schedule instead of surviving rolling breakage. (guess)
- Dependence on Fedora and Universal Blue policy choices.

## 5. Option (d): build on Omarchy's own scripts and layout

What it is
- Omarchy is a Linux distribution by DHH, MIT licensed, with top-level directories bin, install, migrations, config, default, applications, themes, manual, test. Source: https://github.com/basecamp/omarchy
- It installs on Arch with Limine and Snapper, and ships an ISO with full disk encryption and auto login to Hyprland. Source: https://akitaonrails.com/en/2025/09/12/omarchy-2-0-install-with-the-omarchy-iso/
- The Omarchy 4 manual describes snapshot and restore commands (`omarchy-snapshot`). Source: https://raw.githubusercontent.com/basecamp/omarchy/master/manual/47-system-snapshots.md

How changes become reversible
- Same as option (a). Omarchy is option (a) with a finished desktop on top. Its reversibility is the same root-only snapshot.

Agent safety
- Same limits as (a). Bonus: it is already an "agentic" distro in its own words (https://github.com/basecamp/omarchy), and this machine's own rules file shows an agent can drive `omarchy snapshot` and Lua config edits. (From /home/shahid/Work/CLAUDE.md, local.)
- Local note: on this machine, `omarchy snapshot` needs a sudo password in the agent session (from local memory file). So today an agent cannot snapshot unattended. Fixing that means a narrow sudoers rule, which is a design decision for Exo.

Build effort
- Lowest to a working desktop, since Hyprland, themes, menus, update flow, ISO already exist. MIT license permits forking.
- But Exo "replaces Omarchy". A fork means tracking an upstream that moves fast (migrations directory suggests frequent changes, guess) or freezing and owning all of it.
- Config is Lua in 4.0.4 (from local CLAUDE.md), which is a young surface that may keep changing. (guess)

Hardware, VM, USB
- Same as (a). Omarchy publishes an ISO, which gives a ready USB route. Source: akitaonrails link above.

Maintenance cost
- Medium. You inherit its choices (Hyprland, Limine-only snapshots, Direct Boot limits on some firmware). Source for limits: the snapshots manual above.

## 6. Option (e): immutable Arch variants, and Guix

Immutable Arch
- Arkdep is a toolkit for immutable Arch on Btrfs with rollback through deployments. Arkane Linux (immutable GNOME) was built by its developers, and Manjaro tested an immutable spin with it. Sources: https://itsfoss.com/arkane-linux/ and https://www.linux-magazine.com/Online/News/Manjaro-Team-Tests-Immutable-Version-of-its-Arch-Based-Distribution/(tagID)/340
- blendOS v4 calls itself an immutable, atomic, declarative Arch variant, with packages from Ubuntu, Fedora and Debian. Source: https://blendos.co/blog/category/stable/ (vendor claim).
- Immuarch (January 2026) is a toolset for atomic Arch on Btrfs and GRUB, by one developer. Source: https://bbs.archlinux.org/viewtopic.php?id=311910
- Assessment (guess): these give the image-based feel on a familiar Arch base, but they are small projects with small teams. Bus factor and long-term support are real risks. I did not check release dates or activity beyond the sources above.

Guix System
- Guix supports rolling back the whole system to a previous generation, and `guix system reconfigure` applies a config. Source: https://guix.gnu.org/manual/en/html_node/Getting-Started.html
- I did not confirm `guix system vm` or hardware needs from this page (not covered).
- Assessment (guess): same declarative model as NixOS, Scheme config, smaller package set, free-software-only default stance makes proprietary firmware and apps harder. Agent workflow would mirror NixOS.

## 7. Comparison table

Scores are my judgement (guess) built from the sources above. L = low, M = medium, H = high.

| | (a) Arch + btrfs snapshots | (b) NixOS | (c) Fedora Atomic / bootc / Universal Blue | (d) Fork Omarchy | (e) Immutable Arch / Guix |
|---|---|---|---|---|---|
| Machine state reversible | Yes, root only | Yes, every generation | Yes, per deployment | Yes, root only | Yes (Guix, Arkdep deployments) |
| Home and data reversible | No | No | No (kept in /var, not rolled back) | No | No |
| Dry run before applying | No | Yes (`build`, `build-vm`) | Partly (build image, test in VM) | No | Guix yes, Arkdep (guess) partly |
| Agent propose and revert | Snapshot, apply, reboot to undo | Best: config diff, build, test, switch | Good: new image, staged boot | Same as (a) | Guix like Nix, Arkdep like (c) |
| Build effort, small team | L | M to H | M | L to reach desktop, M to own | M to H, thin ecosystem |
| Fit for 5.7 GB RAM | Good | Runtime good, builds need cache or swap | Runtime fine, build in CI | Good | Guix (not verified), Arkdep good |
| Portable device later | x86 fine, ARM weak (guess) | Good (guess) | Good fleet-update model (guess) | x86 only in practice (guess) | Weak |
| VM and USB image | archiso / archinstall / Omarchy ISO | `build-vm`, image from config | bootc-image-builder (formats unverified) | Omarchy ISO exists | Varies |
| Maintenance | M to H (rolling) | L after learning | L to M (fixed releases) | M (track or freeze upstream) | M to H (small projects) |
| Biggest risk | Snapshot is the only net | Learning curve, Nix language | Slow change loop, RPM world | Upstream drift, Limine-only | Project longevity |

## 8. Cross-cutting findings

1. No option snapshots user data. All of them protect the OS, none protect home. Exo's own rule (the log is the source of truth, PRINCIPLES.md section 1) points to the answer: keep Exo state in an append-only log plus periodic btrfs or backup snapshots of the data directory, separate from the OS mechanism. (guess on exact design.)
2. The gate maps well to two layers. Reversible: config changes with a snapshot, generation or deployment behind them. Irreversible: anything outside the OS (sending email, spending money, deleting data without a backup, firmware, disk partitioning). The OS mechanism only covers the first kind. The gate must still classify actions that no rollback can undo.
3. A rollback usually needs a reboot. Reboots are the owner's call under the current Omarchy rules. Exo's gate should treat "needs reboot to revert" as its own class between auto-run and ask. (guess)
4. Agent privilege needs a narrow door. Today an agent cannot snapshot without a sudo password on this machine (local memory). Exo needs a small root helper that exposes only named actions (snapshot, apply, rollback), called by `exod`, never a general root shell. (guess)
5. Declarative config is the cleanest object to approve. A text diff is easy to show in an approval card. NixOS makes the whole system that object. On Arch or Fedora, only a chosen set of files can be.

## 9. Recommendation

Phase 1 (now to first usable Exo OS): option (a) done properly, starting from Omarchy's pieces (option d) as a reference, not as a fork.
- Reason: it is the least work, it already runs on this machine, and the 5.7 GB RAM is not a problem. Exo's value is `exod`, the protocol and the gate. The base should not eat the team's time.
- Take from Omarchy: the Limine plus Snapper plus snap-pac setup, the ISO route, and the idea of migrations. Do not inherit its whole desktop. Keep Exo's own thin layer (a documented list of files `exod` may change, each snapshotted first). MIT license allows reuse. Source: https://github.com/basecamp/omarchy
- Add what Omarchy lacks for an agent: a root helper with named actions, a "pre-change snapshot" step in `exod`, a post-change health check, and an automatic revert path that does not need the owner at a shell (but still respects that reboots are the owner's call).
- Keep a config repo (as hydro-os does today) so the changes `exod` makes are text diffs.

Phase 2 (decide after phase 1 shows real needs): evaluate NixOS or bootc for the portable device and for shipping Exo to other users.
- NixOS gives the best agent loop (build, test, switch, rollback, `build-vm`), and it makes VM and USB images natural. The costs are the Nix learning curve and build RAM. Both are manageable if builds happen in CI or on another machine. (guess)
- bootc and Universal Blue give a signed image pipeline and a fleet-update story, which suits a device that ships to non-technical users. The cost is a slow change loop and a move to Fedora.
- Do not choose between them until a spike answers: can `exod` run as a NixOS module or a bootc image without changes to its Python code? If `exod` stays a plain systemd service with config in /etc and data in /var, the move should be cheap. (guess) So keep `exod` free of Arch-only assumptions from day one (no pacman calls in the core; put distro actions behind one adapter).

Why not the others now
- Fork Omarchy fully: you own a fast-moving upstream and its Lua config, and Exo is meant to replace it.
- Immutable Arch variants: small projects, longevity risk, and they give little over (a) for this team.
- Guix: smaller ecosystem and stricter free-software stance, not verified for this hardware.

Open questions to settle with a short spike (each is a guess until tested)
1. Can a root helper do snapshot, apply, rollback with no sudo password and a strict allow-list? How does that interact with the Omarchy rule that reboots are Shahid's call?
2. How long is a NixOS build on this machine with a binary cache, and does it fit in 5.7 GB with zram?
3. Which bootc-image-builder output types exist today (qcow2, ISO, raw)? Not confirmed in this pass.
4. Does a Hyprland-based Exo desktop on Fedora Atomic exist or need custom work?
5. What is the right way to protect user data (btrfs home snapshots, log replication, backup)? No option solves this for free.

## 10. Sources fetched or searched in this pass

- https://github.com/basecamp/omarchy
- https://raw.githubusercontent.com/basecamp/omarchy/master/manual/47-system-snapshots.md
- https://akitaonrails.com/en/2025/09/12/omarchy-2-0-install-with-the-omarchy-iso/
- https://github.com/wesbarnett/snap-pac
- https://github.com/archlinux/archinstall
- https://nixos.org/manual/nixos/stable/
- https://discourse.nixos.org/t/best-practice-for-running-nixos-on-low-end-system/77592
- https://discourse.nixos.org/t/adding-nixos-entry-to-libosinfo-recommended-resources/6441
- https://bootc.dev/bootc/
- https://universal-blue.org/
- https://github.com/osbuild/bootc-image-builder
- https://github.com/justinkinney/rpm-ostree/blob/main/docs/index.md
- https://discussion.fedoraproject.org/t/experiences-from-testing-fedora-silverblue/21528
- https://gitlab.com/fedora/ostree/sig/-/issues/26
- https://itsfoss.com/arkane-linux/
- https://www.linux-magazine.com/Online/News/Manjaro-Team-Tests-Immutable-Version-of-its-Arch-Based-Distribution/(tagID)/340
- https://blendos.co/blog/category/stable/
- https://bbs.archlinux.org/viewtopic.php?id=311910
- https://guix.gnu.org/manual/en/html_node/Getting-Started.html

Gaps: the Arch wiki Snapper page was blocked by bot protection, so snapper details come from snap-pac's README. Some pages were read through search summaries rather than in full, and are marked "via search summary". No Fedora minimum RAM, no Guix hardware needs, no bootc-image-builder formats were confirmed.

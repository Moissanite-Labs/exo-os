# Administrator boot recovery

This is the manual recovery path for the current **unsigned** x86-64 mkosi image with
systemd-boot and UEFI Secure Boot disabled. It adds no Exo tool or exo-sysd action.
An administrator uses a root console or the test image's authenticated root SSH.
The person's account has no sudo permission to run it.

Before a risky system change:

```sh
exo-os-recovery checkpoint
# Save the JSON id from this command.
```

To make that checkpoint available at next boot:

```sh
exo-os-recovery stage CHECKPOINT_ID
```

Then the administrator reboots and chooses **Exo OS recovery ID** from the
systemd-boot menu (five-second timeout). Staging does not reboot, change the
regular default, install a bootloader, change partition layout, or overwrite `@`.

The checkpoint is a native read-only btrfs snapshot plus an archived UKI containing
its kernel, initrd and original command line. Its manifest binds a checksum,
kernel version, snapshot UUID and filesystem UUID. These artifacts live under
`/.snapshots/exo-recovery/checkpoints/ID`, outside snapper's numbered snapshot
cleanup. They are root-owned and not writable by the person. The tool takes
pacman's native database lock for the operation; a package transaction already
in progress makes it refuse. Administrators must keep other system writers idle
while making a checkpoint. A filesystem snapshot does not freeze arbitrary
running application state.

Staging creates a writable clone under
`@snapshots/exo-recovery/roots/ID`, copies the archived UKI onto the mounted ESP at
`EFI/exo-recovery/ID.efi`, and publishes a Boot Loader Specification Type #1 entry
last. That entry changes only `rootflags=subvol=...`; the UKI's root-device and
console options remain intact. `@home`, `@store`, `@sysd`, `@egress`, `@signer` and `@snapshots` are separately
mounted from the existing filesystem according to the checked snapshot fstab.
User data and the principal's signed ledger therefore stay current while system
files return to the checkpoint. The original `@` remains available as the regular
boot entry. Recovery is a separate working root, not promotion of the clone to `@`.
The recovered root cannot itself create another checkpoint with this first version.
Staging removes the checkpoint-era pacman database lock only from this writable
clone. The read-only checkpoint retains its captured file and the original system's
live lock remains held until the administrator operation finishes.

The helper's signing key and root audit log live on the separate persistent
`@sysd` mount at `/var/lib/exo-sysd`; the caller's receipt outbox stays on `@store`.
These remain current across recovery, together with the principal ledger.
`exo-sysd.service` requires its state mount and asserts that it is a mount point,
so a missing mount cannot silently create a replacement identity on the system
root. The helper state directory is root-owned mode 0700. Recovery checks the
same btrfs filesystem UUID and exact `@sysd` mount/fstab entry before checkpoint
or staging. The snapshot excludes this mounted helper state.

The ledger authority persists separately on `@signer` at `/var/lib/exo-ledger`.
Its public manifest stays root-owned; private keys and committed history belong to
`exo-ledger`. Boot validates existing identity and journal before permitting login,
including when an older system root is selected. Recovery cannot reseed missing
established state or rewind post-checkpoint ledger commits.

The egress broker's journal mounts from persistent `@egress` at
`/var/lib/exo-egress` under its separate service UID. Recovery requires that exact
mount on the same btrfs filesystem, both live and in the checkpoint's fstab.
Reservations and completed/uncertain outcomes therefore stay current across a
system rollback: an older root cannot forget an operation and send it again.
The egress service likewise requires its mount before it starts. The system
snapshot excludes this state along with home, store and helper state.

This is a **fresh-image layout**. Existing installations with helper state on
`@`, or a root audit log at the old `/var/log/exo-sysd.log` location, need a separate
administrator migration before adopting it. The recovery command provides no live
migration and rejects checkpoints without the persistent helper mount contract.

Only checkpoints made by this command are eligible. A plain snapper snapshot
without archived boot artifacts is not claimed bootable: the EFI partition is
outside the btrfs system snapshot, and a newer kernel may not match older modules.
The tool refuses nonmatching mounts, unsafe paths/symlinks, writable metadata,
changed snapshot identities/checksums, missing matched modules, ambiguous UKIs,
and UKI companion resources. It rejects unavailable or enabled Secure Boot state.
If Secure Boot is later enabled, these unsigned entries are unsupported: do not
attempt to rely on a previously staged recovery entry. Signed recovery UKIs with
embedded per-clone command lines need a separate signing/enrollment design.

Repeated staging refuses existing destinations. An interrupted stage may leave a
clone or UKI, but no menu entry is published until both exist and have been synced.
An administrator inspects those artifacts manually before deleting or retrying.
No automatic garbage collection, snapshot deletion, boot promotion, rollback of
user data, firmware changes or automatic failure detection is implemented.

## Protected state refused before login

`exo-ledger-provision.service` and `exo-model-spend-provision.service` run before
login, and a refusal from either blocks it. A power cut during first-boot
provisioning no longer does. The next boot finishes or restarts provisioning when
the state shows nothing was signed, published or used. See
[the ledger rules](LEDGER-AUTHORITY.md#provision-cut-off-before-publication) and
[the spend rules](MODEL-SPEND.md#first-boot-cut-off-before-publication). Every other
refusal ends with a short hint in the unit's log. Where the state still shows
nothing was published, the hint names a root repair command. From a root console:

```sh
journalctl -b -u exo-ledger-provision.service -u exo-model-spend-provision.service
exo-ledger-admin repair          # only if the ledger unit failed
exo-model-spend-admin repair     # only if the spend unit failed
reboot
```

Both commands leave state that passes the boot check in place. Otherwise they act
only on state that shows nothing was signed, published or used. They move it into a
new root-only directory on the same subvolume, under `/var/lib/exo-ledger/repair/`
or `/var/lib/exo/model-spend-repair/`, and provision fresh state. Nothing is
deleted. State that may hold established history refuses unchanged and needs a
separately reviewed migration. A new ledger genesis never replaces published
history, so OS-17 is unchanged. A recovery checkpoint does not roll these
directories back, because they live on the persistent `@signer` and `@store`
subvolumes.

## Why systemd-boot can select the clone

The [Boot Loader Specification](https://uapi-group.org/specifications/specs/boot_loader_specification/)
defines Type #1 `efi` entries with command-line options and paths relative to the
same boot partition. [systemd-stub's official documentation](https://github.com/systemd/systemd/blob/main/man/systemd-stub.xml)
says an EFI invocation can override the embedded command line when Secure Boot is
disabled; with Secure Boot enabled and an embedded `.cmdline`, it ignores that
override. The current image explicitly builds unsigned UKIs, so this path is
narrowly limited to its disabled-Secure-Boot configuration. The loader editor is
disabled; command-line changes are prepared by the root administrator.

## Acceptance and evidence

```sh
tools/build-image.sh --profile=vmtest
tools/accept-recovery.sh
```

The acceptance harness starts a throwaway overlay, checks the VM's unique UUID,
makes a checkpoint, writes newer home/store data and a changed system marker,
checks that systemd-boot recognizes the menu entry, selects it once and **actually
reboots**. It then verifies a changed boot ID, the same VM identity, the recovery
root mount, the older system marker, current home/store mounts and data, unchanged
principal-store hashes with exod masked across the entire test, a valid signed
ledger, absence of a stale pacman lock on the recovery root, and preservation of
original `@`. It also verifies the actual persistent helper mount, unchanged
signing-key/public-key hashes (never key material), and preservation of a real
root audit receipt made after the checkpoint; the recovered helper must start.
A synthetic egress operation is reserved in the real service-owned journal
**after** the checkpoint. Following the older-root reboot, the script checks the
exact journal row, restarts the broker, and calls it over its authenticated socket:
the operation must remain reserved, return `unknown` with `retryable=false`, and
leave the row unchanged. No API access, provider policy or live credentials are
provisioned for this check.
The script also appends a synthetic signed ledger record after the checkpoint,
then verifies unchanged public identity/private-key hashes and the exact committed
operation through the authenticated signer after the older-root boot and service
restart. Key material is never copied into acceptance reports.
Any missing prerequisite, malformed output, wrong VM or missed reboot fails.
A successful host fixture test is not evidence of an actual boot. The report is
`image/out/accept-recovery.txt` when this script is run.

The new source flow retains those ten checks and adds an eleventh, named
`return-to-normal`. It selects the normal boot entry, reboots again, and requires
a new boot ID, the same VM UUID, original `@` and its marker, and unchanged
home/store, signer, helper, spend and egress state. Protected service-directory
modes are checked again without repair. Separate return evidence records the
original ten-check count. This eleven-check flow has not run in a VM; controlled
shell fixtures do not establish a real reboot. Source using core `8600bce` needs
a fresh image before native acceptance.

On 2026-10-13, the older `5a86e3b` image with core `cff62d1` passed the historical ten checks
through an actual older-root reboot. Current home/store, signer history, helper
audit, model-spend stop and synthetic egress reservation survived. The first
attempt ended in host OOM and remains retained separately.

The built `c2d1ad9` image still needs its own recovery acceptance. Witness receipt
persistence, power-loss behavior and hardware/soak checks remain unrun. Exact
identities, report hashes and limits are in
[VALIDATION.md](VALIDATION.md#historical-image-5a86e3b).

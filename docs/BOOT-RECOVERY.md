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
console options remain intact. `@home`, `@store` and `@snapshots` are separately
mounted from the existing filesystem according to the checked snapshot fstab.
User data and the principal's signed ledger therefore stay current while system
files return to the checkpoint. The original `@` remains available as the regular
boot entry. Recovery is a separate working root, not promotion of the clone to `@`.
The recovered root cannot itself create another checkpoint with this first version.

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
principal-store hashes with exod stopped at both measurements, a valid signed ledger, and preservation of original `@`.
Any missing prerequisite, malformed output, wrong VM or missed reboot fails.
A successful host fixture test is not evidence of an actual boot. The report is
`image/out/accept-recovery.txt` when this script is run; this implementation has
not yet passed a real VM reboot.

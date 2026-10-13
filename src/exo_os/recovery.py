"""Manual administrator recovery for the unsigned systemd-boot image.

Not a sysd action or agent tool. Nothing here selects a boot entry or reboots.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import uuid


class RecoveryError(RuntimeError):
    pass


def command(*argv: str) -> str:
    try:
        return subprocess.run(("/usr/bin/" + argv[0], *argv[1:]), check=True, capture_output=True, text=True, timeout=120).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecoveryError(f"Required command failed: {argv[0]}") from exc


def plain_path(path: Path) -> Path:
    """Reject symlinks at every component, including not-yet-created leaf paths."""
    if not path.is_absolute() or path != path.resolve():
        raise RecoveryError(f"Unsafe path: {path}")
    return path


def secure_file(path: Path, owner_uid: int = 0) -> None:
    plain_path(path)
    info = path.stat()
    if info.st_uid != owner_uid or info.st_mode & 0o022:
        raise RecoveryError(f"Recovery metadata must be root-owned and not writable by others: {path}")


def uki_sections(data: bytes) -> dict[str, bytes]:
    """Read bounded single-profile PE sections; no external parser or executable."""
    try:
        if data[:2] != b"MZ":
            raise ValueError()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise ValueError()
        machine, count = struct.unpack_from("<HH", data, pe + 4)
        optional = struct.unpack_from("<H", data, pe + 20)[0]
        if machine != 0x8664 or not 1 <= count <= 64:
            raise ValueError()
        table = pe + 24 + optional
        sections = {}
        for i in range(count):
            pos = table + 40 * i
            name = data[pos:pos + 8].rstrip(b"\0").decode("ascii")
            virtual_size, _, size, offset = struct.unpack_from("<IIII", data, pos + 8)
            if offset + size > len(data) or name in sections or name == ".profile":
                raise ValueError()
            sections[name] = data[offset:offset + min(size, virtual_size)]
        if not all(sections.get(k) for k in (".linux", ".initrd", ".uname", ".cmdline")):
            raise ValueError()
        return sections
    except (ValueError, struct.error, UnicodeError):
        raise RecoveryError("Not a supported x86-64 single-profile UKI") from None


def text_section(sections: dict[str, bytes], key: str) -> str:
    try:
        value = sections[key].rstrip(b"\0").decode("utf-8").strip()
        if not value or any(c in value for c in "\n\r\0"):
            raise ValueError()
        return value
    except (UnicodeError, ValueError):
        raise RecoveryError(f"Invalid UKI {key}") from None


def recovery_options(cmdline: str, subvolume: str) -> str:
    # Restrict to this image's simple unquoted command line. Do not silently
    # discard other btrfs options or change its root-device discovery contract.
    if any(c in cmdline for c in "\n\r\0\"'"):
        raise RecoveryError("Unsupported kernel command line")
    words = cmdline.split()
    flags = [w for w in words if w.startswith("rootflags=")]
    if len(flags) != 1 or flags[0] != "rootflags=subvol=@":
        raise RecoveryError("Checkpoint UKI must select the original @ system root")
    if sum(w.startswith("root=") for w in words) > 1:
        raise RecoveryError("Ambiguous root device")
    return " ".join("rootflags=subvol=" + subvolume if w == flags[0] else w for w in words)


def check_fstab(path: Path) -> None:
    plain_path(path)
    expected = {"/home": "@home", "/.snapshots": "@snapshots", "/var/lib/exo": "@store", "/var/lib/exo-sysd": "@sysd",
                "/var/lib/exo-egress": "@egress", "/var/lib/exo-ledger": "@signer"}
    found = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) < 4 or fields[1] == "/":
            raise RecoveryError("Unsupported snapshot fstab (root must come from UKI)")
        if fields[1] in expected:
            if fields[1] in found or fields[0] != "/dev/gpt-auto-root" or fields[2] != "btrfs":
                raise RecoveryError("Snapshot does not preserve the image's data mounts")
            options = fields[3].split(",")
            subvol = [o for o in options if o.startswith(("subvol=", "subvolid="))]
            if subvol != ["subvol=" + expected[fields[1]]]:
                raise RecoveryError("Snapshot does not preserve the image's data mounts")
            found[fields[1]] = True
    if set(found) != set(expected):
        raise RecoveryError("Snapshot is missing separate home/store/helper/egress/signer/snapshot mounts")


class Recovery:
    def __init__(self, *, root=Path("/"), snapshots=Path("/.snapshots"), esp=Path("/efi"), run=command):
        self.root, self.snapshots, self.esp, self.run = root, snapshots, esp, run
        self.base = snapshots / "exo-recovery"

    def trusted_path(self, path: Path, anchor: Path) -> None:
        plain_path(path)
        current = path
        while True:
            if current.exists():
                secure_file(current)
            if current == anchor:
                break
            if current == current.parent:
                raise RecoveryError("Path is outside its trusted mount")
            current = current.parent

    def mount_row(self, path: Path, fields: str) -> dict:
        try:
            data = json.loads(self.run("findmnt", "--json", "--mountpoint", str(path),
                                       "--output", fields))
            rows = data["filesystems"]
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError()
            return rows[0]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RecoveryError(f"Cannot identify image mount: {path}") from None

    def esp_mount(self) -> dict:
        row = self.mount_row(self.esp, "TARGET,FSTYPE")
        if row.get("target") == str(self.esp) and row.get("fstype") == "autofs":
            # systemd-gpt-auto-generator exposes the ESP through efi.automount.
            # Activate only that known root-image mount, then validate the
            # resulting filesystem exactly as for an already-mounted ESP.
            if self.root != Path("/") or self.esp != Path("/efi"):
                raise RecoveryError("EFI system partition is not mounted")
            self.run("systemctl", "start", "efi.mount")
            row = self.mount_row(self.esp, "TARGET,FSTYPE")
        if row.get("target") != str(self.esp) or row.get("fstype") != "vfat":
            raise RecoveryError("EFI system partition is not mounted")
        return row

    def preflight(self) -> str:
        # Exact mount identity matters: a directory on @ is not a data subvolume.
        for path in (self.root, self.snapshots, self.esp):
            plain_path(path)
        rows = []
        for path, fsroot in ((self.root, "/@"), (self.root / "home", "/@home"),
                             (self.root / "var/lib/exo", "/@store"), (self.root / "var/lib/exo-sysd", "/@sysd"),
                             (self.root / "var/lib/exo-egress", "/@egress"),
                             (self.root / "var/lib/exo-ledger", "/@signer"), (self.snapshots, "/@snapshots")):
            row = self.mount_row(path, "TARGET,FSTYPE,FSROOT,UUID")
            if row["target"] != str(path) or row["fstype"] != "btrfs" or row["fsroot"] != fsroot:
                raise RecoveryError(f"Unexpected image mount: {path}")
            rows.append(row)
        fs_uuid = rows[0]["uuid"]
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", fs_uuid or "") or any(r["uuid"] != fs_uuid for r in rows):
            raise RecoveryError("System and data must be on the image's one btrfs filesystem")
        self.esp_mount()
        # Companions could change cmdline/initrd outside the archived UKI.
        for p in (self.esp / "loader/addons", self.esp / "loader/extensions",
                  self.esp / "loader/credentials"):
            if p.exists() and any(p.iterdir()):
                raise RecoveryError("UKI global companion resources are unsupported")
        self.trusted_path(self.base, self.snapshots)
        self.trusted_path(self.esp, self.esp)
        return fs_uuid

    def subvolume_uuid(self, path: Path) -> str:
        plain_path(path)
        output = self.run("btrfs", "subvolume", "show", str(path))
        match = re.search(r"^\s*UUID:\s*([0-9a-f-]{36})\s*$", output, re.M)
        if not match:
            raise RecoveryError("Cannot identify recovery subvolume")
        return match[1]

    def checkpoint(self) -> dict:
        fs_uuid = self.preflight()
        check_fstab(self.root / "etc/fstab")
        candidates = []
        for path in sorted((self.esp / "EFI/Linux").glob("*.efi")):
            self.trusted_path(path, self.esp)
            if path.with_name(path.name + ".extra.d").exists():
                raise RecoveryError("UKI companion resources are unsupported")
            data = path.read_bytes()
            sections = uki_sections(data)
            kernel = text_section(sections, ".uname")
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", kernel):
                raise RecoveryError("Unsafe kernel version")
            plain_path(self.root / "usr/lib/modules" / kernel)
            if not (self.root / "usr/lib/modules" / kernel).is_dir():
                continue
            cmdline = text_section(sections, ".cmdline")
            recovery_options(cmdline, "@snapshots/exo-recovery/roots/check")
            candidates.append((data, kernel, cmdline))
        if len(candidates) != 1:
            raise RecoveryError("Require exactly one installed UKI matching the system's kernel modules")
        ident = uuid.uuid4().hex
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive = self.base / "checkpoints" / ident
        archive.mkdir(mode=0o700, parents=True)
        data, kernel, cmdline = candidates[0]
        uki = archive / "kernel.efi"
        uki.write_bytes(data)
        uki.chmod(0o600)
        self.run("btrfs", "subvolume", "snapshot", "-r", str(self.root), str(archive / "root"))
        manifest = {"version": 1, "id": ident, "filesystem_uuid": fs_uuid,
                    "snapshot_uuid": self.subvolume_uuid(archive / "root"), "kernel": kernel,
                    "sha256": hashlib.sha256(data).hexdigest(), "cmdline": cmdline}
        self.write_json(archive / "manifest.json", manifest)
        self.run("sync", "-f", str(archive))
        return manifest

    @staticmethod
    def write_json(path: Path, data: dict) -> None:
        with path.open("x") as handle:
            json.dump(data, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())

    def stage(self, ident: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", ident):
            raise RecoveryError("Invalid checkpoint ID")
        fs_uuid = self.preflight()
        archive = self.base / "checkpoints" / ident
        for path in (archive, archive / "manifest.json", archive / "kernel.efi"):
            self.trusted_path(path, self.snapshots)
        manifest = json.loads((archive / "manifest.json").read_text())
        if (manifest.get("version") != 1 or manifest.get("id") != ident or
                manifest.get("filesystem_uuid") != fs_uuid):
            raise RecoveryError("Checkpoint belongs to another image or manifest version")
        snapshot = archive / "root"
        if self.subvolume_uuid(snapshot) != manifest.get("snapshot_uuid"):
            raise RecoveryError("Checkpoint snapshot identity changed")
        if self.run("btrfs", "property", "get", "-ts", str(snapshot), "ro") != "ro=true":
            raise RecoveryError("Checkpoint snapshot must remain read-only")
        data = (archive / "kernel.efi").read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            raise RecoveryError("Checkpoint UKI checksum changed")
        sections = uki_sections(data)
        kernel = text_section(sections, ".uname")
        cmdline = text_section(sections, ".cmdline")
        if kernel != manifest.get("kernel") or cmdline != manifest.get("cmdline"):
            raise RecoveryError("Checkpoint UKI does not match its manifest")
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", kernel):
            raise RecoveryError("Unsafe checkpoint kernel version")
        plain_path(snapshot / "usr/lib/modules" / kernel)
        if not (snapshot / "usr/lib/modules" / kernel).is_dir():
            raise RecoveryError("Checkpoint lacks matched kernel modules")
        check_fstab(snapshot / "etc/fstab")
        subvol = "@snapshots/exo-recovery/roots/" + ident
        options = recovery_options(cmdline, subvol)
        clone = self.base / "roots" / ident
        boot = self.esp / "EFI/exo-recovery" / (ident + ".efi")
        entry = self.esp / "loader/entries" / ("exo-recovery-" + ident + ".conf")
        for path in (clone, boot, entry):
            self.trusted_path(path, self.snapshots if path == clone else self.esp)
            if path.exists():
                raise RecoveryError("Recovery already staged or unfinished; inspect artifacts manually")
        clone.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        boot.parent.mkdir(parents=True, exist_ok=True)
        entry.parent.mkdir(parents=True, exist_ok=True)
        # Publish entry last: partial failures never offer an incomplete recovery boot.
        self.run("btrfs", "subvolume", "snapshot", str(snapshot), str(clone))
        # checkpoint runs while we hold pacman's lock on @. Its snapshot keeps
        # that transient file, but the future boot must not inherit a stale lock.
        # Remove only the copy, never the live lock or the read-only checkpoint.
        copied_lock = clone / "var/lib/pacman/db.lck"
        plain_path(copied_lock)
        if copied_lock.exists():
            secure_file(copied_lock)
            if not stat.S_ISREG(copied_lock.stat().st_mode):
                raise RecoveryError("Checkpoint pacman lock must be a regular file")
            copied_lock.unlink()
        with boot.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self.run("sync", "-f", str(clone))
        self.run("sync", "-f", str(boot))
        content = (f"title Exo OS recovery {ident[:8]}\nversion {kernel}\nsort-key zzz-exo-recovery\n"
                   f"efi /EFI/exo-recovery/{ident}.efi\noptions {options}\n")
        temporary = entry.with_suffix(".pending")
        plain_path(temporary)
        with temporary.open("x") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.rename(entry)
        self.run("sync", "-f", str(entry))
        return {"id": ident, "entry": entry.name, "rootflags": "subvol=" + subvol}


def firmware_check() -> None:
    secureboot = Path("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")
    try:
        value = secureboot.read_bytes()
    except OSError:
        raise RecoveryError("Cannot establish UEFI Secure Boot state; recovery requires it disabled") from None
    if len(value) != 5 or value[4] != 0:
        raise RecoveryError("Recovery menu options require Secure Boot disabled for this unsigned image")


@contextmanager
def administrator_lock():
    # Use pacman's native lock too: checkpoints must not race a package transaction.
    with open("/run/exo-os-recovery.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        package_lock = Path("/var/lib/pacman/db.lck")
        try:
            descriptor = os.open(package_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise RecoveryError("Package transaction in progress; try after it finishes") from None
        try:
            os.close(descriptor)
            yield
        finally:
            package_lock.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Administrator-only unsigned-image recovery; never selects or reboots")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("checkpoint", help="archive a read-only system snapshot and its matching UKI")
    stage = sub.add_parser("stage", help="prepare a writable clone and a recovery boot-menu entry")
    stage.add_argument("id")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise RecoveryError("Run manually as a Linux root administrator")
        firmware_check()
        esp = Path(command("bootctl", "--print-esp-path"))
        if esp not in (Path("/efi"), Path("/boot")):
            raise RecoveryError("Unsupported image EFI mount path")
        with administrator_lock():
            recovery = Recovery(esp=esp)
            result = recovery.checkpoint() if args.action == "checkpoint" else recovery.stage(args.id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RecoveryError, OSError, ValueError, KeyError, IndexError) as exc:
        print(f"Recovery refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

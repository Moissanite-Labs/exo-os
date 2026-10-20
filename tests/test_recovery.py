"""Recovery operates only in these directory/command fixtures, never host boot/data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct

import pytest

from exo_os import recovery as r

FS_UUID = "22222222-2222-2222-2222-222222222222"
SNAP_UUID = "33333333-3333-3333-3333-333333333333"
KERNEL = "6.17.1-arch1-1"
FSTAB = """/dev/gpt-auto-root /home btrfs subvol=@home,compress=zstd 0 0
/dev/gpt-auto-root /.snapshots btrfs subvol=@snapshots,compress=zstd 0 0
/dev/gpt-auto-root /var/lib/exo btrfs subvol=@store,compress=zstd 0 0
/dev/gpt-auto-root /var/lib/exo-sysd btrfs subvol=@sysd,compress=zstd 0 0
/dev/gpt-auto-root /var/lib/exo-egress btrfs subvol=@egress,compress=zstd 0 0
/dev/gpt-auto-root /var/lib/exo-ledger btrfs subvol=@signer,compress=zstd 0 0
"""


def test_fstab_without_persistent_signer_refuses_recovery(tmp_path):
    path = (tmp_path / "fstab").resolve()
    path.write_text("\n".join(line for line in FSTAB.splitlines() if "/var/lib/exo-ledger" not in line))
    with pytest.raises(r.RecoveryError, match="signer"):
        r.check_fstab(path)


def test_fstab_signer_on_system_root_refuses_recovery(tmp_path):
    path = (tmp_path / "fstab").resolve()
    path.write_text(FSTAB.replace("subvol=@signer", "subvol=@"))
    with pytest.raises(r.RecoveryError):
        r.check_fstab(path)


def uki(cmdline="rw quiet rootflags=subvol=@", kernel=KERNEL):
    sections = {".linux": b"kernel", ".initrd": b"initrd", ".uname": kernel.encode() + b"\0",
                ".cmdline": cmdline.encode() + b"\0"}
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<HH", data, 68, 0x8664, len(sections))
    struct.pack_into("<H", data, 84, 0)
    for i, (name, value) in enumerate(sections.items()):
        pos = 88 + i * 40
        data[pos:pos + 8] = name.encode().ljust(8, b"\0")
        struct.pack_into("<IIII", data, pos + 8, len(value), 0, len(value), len(data))
        data.extend(value)
    return bytes(data)


@pytest.fixture
def image(tmp_path, monkeypatch):
    root, snapshots, esp = (tmp_path / name for name in ("root", "snapshots", "esp"))
    for path in (root / "etc", root / "usr/lib/modules" / KERNEL, root / "home",
                 root / "var/lib/exo", root / "var/lib/exo-sysd", root / "var/lib/exo-egress",
                 root / "var/lib/exo-ledger", snapshots, esp / "EFI/Linux"):
        path.mkdir(parents=True, exist_ok=True)
    (root / "etc/fstab").write_text(FSTAB)
    (root / "etc/revision").write_text("old system")
    (root / "home/person").write_text("current home")
    (root / "var/lib/exo/ledger").write_text("current store")
    (root / "var/lib/exo-sysd/sysd.key").write_text("fixture-key-never-a-real-key")
    (root / "var/lib/exo-sysd/audit.log").write_text("old root receipt")
    (esp / "EFI/Linux/exo-os.efi").write_bytes(uki())
    calls = []
    readonly = set()

    def run(*args):
        calls.append(args)
        if args[0] == "findmnt":
            assert "--uniq" in args
            path = Path(args[args.index("--mountpoint") + 1])
            roots = {root: "/@", root / "home": "/@home", root / "var/lib/exo": "/@store", root / "var/lib/exo-sysd": "/@sysd", root / "var/lib/exo-egress": "/@egress", root / "var/lib/exo-ledger": "/@signer", snapshots: "/@snapshots"}
            row = {"target": str(path), "fstype": "vfat" if path == esp else "btrfs",
                   "fsroot": roots.get(path), "uuid": FS_UUID}
            return json.dumps({"filesystems": [row]})
        if args[:3] == ("btrfs", "subvolume", "snapshot"):
            source, target = map(Path, args[-2:])
            # Separate mount data do not belong to the native root snapshot.
            def separate_mounts(directory, names):
                directory = Path(directory)
                if directory == source:
                    return ["home"] if "home" in names else []
                if directory == source / "var/lib":
                    return [name for name in ("exo", "exo-sysd", "exo-egress", "exo-ledger") if name in names]
                return []
            shutil.copytree(source, target, ignore=separate_mounts, symlinks=True)
            if "-r" in args:
                readonly.add(str(target))
            return ""
        if args[:3] == ("btrfs", "subvolume", "show"):
            return "UUID: " + SNAP_UUID
        if args[:3] == ("btrfs", "property", "get"):
            return "ro=true" if args[-2] in readonly else "ro=false"
        if args[0] == "sync":
            return ""
        raise AssertionError(args)

    original = r.secure_file
    monkeypatch.setattr(r, "secure_file", lambda path: original(path, os.getuid()))
    helper = r.Recovery(root=root, snapshots=snapshots, esp=esp, run=run)
    return helper, root, snapshots, esp, calls, readonly


def test_checkpoint_stage_preserves_home_store_and_original_root(image):
    helper, root, snapshots, esp, calls, _ = image
    checkpoint = helper.checkpoint()
    (root / "etc/revision").write_text("broken system")
    (root / "home/person").write_text("newer home")
    (root / "var/lib/exo/ledger").write_text("newer store")
    # ESP no longer has the checkpoint kernel; stage must use the archive.
    (esp / "EFI/Linux/exo-os.efi").write_bytes(uki(kernel="later"))
    staged = helper.stage(checkpoint["id"])
    clone = snapshots / "exo-recovery/roots" / checkpoint["id"]
    assert (clone / "etc/revision").read_text() == "old system"
    assert (root / "etc/revision").read_text() == "broken system"
    assert (root / "home/person").read_text() == "newer home"
    assert (root / "var/lib/exo/ledger").read_text() == "newer store"
    entry = (esp / "loader/entries" / staged["entry"]).read_text()
    assert f"rootflags=subvol=@snapshots/exo-recovery/roots/{checkpoint['id']}" in entry
    assert "efi /EFI/exo-recovery/" in entry
    assert hashlib.sha256((esp / "EFI/exo-recovery" / (checkpoint["id"] + ".efi")).read_bytes()).hexdigest() == checkpoint["sha256"]
    assert not any(args[0] in ("bootctl", "reboot", "systemctl") for args in calls)
    assert not any("delete" in args or "set-default" in args for args in calls)
    with pytest.raises(r.RecoveryError, match="already staged"):
        helper.stage(checkpoint["id"])


@pytest.mark.parametrize("ident", ["../root", "/etc", "a" * 31, "A" * 32, "a\n" * 16, "0;reboot"])
def test_stage_rejects_unsafe_id_before_commands(image, ident):
    helper, *_, calls, _ = image
    with pytest.raises(r.RecoveryError, match="Invalid"):
        helper.stage(ident)
    assert calls == []


@pytest.mark.parametrize("field,value", [("id", "a" * 32), ("filesystem_uuid", "other"),
                                         ("snapshot_uuid", "other"), ("version", 2),
                                         ("sha256", "other"), ("kernel", "other"), ("cmdline", "other")])
def test_manifest_tampering_is_rejected_before_clone(image, field, value):
    helper, _, snapshots, _, calls, _ = image
    checkpoint = helper.checkpoint()
    path = snapshots / "exo-recovery/checkpoints" / checkpoint["id"] / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    calls.clear()
    with pytest.raises(r.RecoveryError):
        helper.stage(checkpoint["id"])
    assert not any(args[:3] == ("btrfs", "subvolume", "snapshot") for args in calls)


@pytest.mark.parametrize("name", ["manifest.json", "kernel.efi", "root"])
def test_checkpoint_symlink_rejected(image, name, tmp_path):
    helper, _, snapshots, _, _, _ = image
    checkpoint = helper.checkpoint()
    path = snapshots / "exo-recovery/checkpoints" / checkpoint["id"] / name
    outside = tmp_path / ("outside-" + name)
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=name == "root")
    with pytest.raises(r.RecoveryError, match="Unsafe path"):
        helper.stage(checkpoint["id"])


def test_writable_manifest_rejected(image):
    helper, _, snapshots, *_ = image
    checkpoint = helper.checkpoint()
    path = snapshots / "exo-recovery/checkpoints" / checkpoint["id"] / "manifest.json"
    path.chmod(0o666)
    with pytest.raises(r.RecoveryError, match="not writable"):
        helper.stage(checkpoint["id"])


def test_writable_snapshot_rejected(image):
    helper, *_, readonly = image
    checkpoint = helper.checkpoint()
    readonly.clear()
    with pytest.raises(r.RecoveryError, match="read-only"):
        helper.stage(checkpoint["id"])


@pytest.mark.parametrize("data", [b"invalid", b"MZ", uki()[:520]])
def test_malformed_uki_rejected(data):
    with pytest.raises(r.RecoveryError):
        r.uki_sections(data)


@pytest.mark.parametrize("cmdline", ["rw", "rw rootflags=subvol=@home", "rootflags=subvol=@ rootflags=subvol=@",
                                     "rootflags=subvol=@\nreboot", 'rootflags=subvol=@ "other"'])
def test_unsafe_root_command_line_rejected(cmdline):
    with pytest.raises(r.RecoveryError):
        r.recovery_options(cmdline, "@snapshots/exo-recovery/roots/test")


def test_root_device_and_console_options_preserved():
    assert r.recovery_options("root=PARTUUID=abc rw rootflags=subvol=@ console=ttyS0,115200", "@snapshots/clone") == (
        "root=PARTUUID=abc rw rootflags=subvol=@snapshots/clone console=ttyS0,115200")


@pytest.mark.parametrize("edit", [lambda s: s.replace("@home", "@"), lambda s: s.replace("@store", "@home"),
                                  lambda s: s + "/dev/gpt-auto-root / btrfs subvol=@ 0 0\n",
                                  lambda s: s.replace("/dev/gpt-auto-root /home", "UUID=other /home")])
def test_wrong_data_mounts_rejected(image, edit):
    helper, root, *_ = image
    (root / "etc/fstab").write_text(edit(FSTAB))
    with pytest.raises(r.RecoveryError):
        helper.checkpoint()


def test_root_esp_automount_is_activated_and_revalidated():
    calls = []
    activated = False

    def run(*args):
        nonlocal activated
        calls.append(args)
        if args[0] == "systemctl":
            assert args == ("systemctl", "start", "efi.mount")
            activated = True
            return ""
        assert args[0] == "findmnt"
        assert "--uniq" in args
        return json.dumps({"filesystems": [{"target": "/efi", "fstype": "vfat" if activated else "autofs"}]})

    helper = r.Recovery(root=Path("/"), esp=Path("/efi"), run=run)
    assert helper.esp_mount() == {"target": "/efi", "fstype": "vfat"}
    assert [call[0] for call in calls] == ["findmnt", "systemctl", "findmnt"]


@pytest.mark.parametrize("filesystems", [[], [{"target": "/efi", "fstype": "ext4"}],
                                            [{"target": "/other", "fstype": "vfat"}]])
def test_missing_or_wrong_esp_mount_is_refused(filesystems):
    helper = r.Recovery(root=Path("/"), esp=Path("/efi"),
                        run=lambda *args: json.dumps({"filesystems": filesystems}))
    with pytest.raises(r.RecoveryError, match="mount"):
        helper.esp_mount()


def test_esp_automount_that_does_not_become_vfat_is_refused():
    calls = []

    def run(*args):
        calls.append(args)
        if args[0] == "systemctl":
            return ""
        return json.dumps({"filesystems": [{"target": "/efi", "fstype": "autofs"}]})

    helper = r.Recovery(root=Path("/"), esp=Path("/efi"), run=run)
    with pytest.raises(r.RecoveryError, match="not mounted"):
        helper.esp_mount()
    assert [call[0] for call in calls] == ["findmnt", "systemctl", "findmnt"]


def test_esp_automount_is_supported_only_at_the_image_root(tmp_path):
    esp = tmp_path / "efi"
    helper = r.Recovery(root=tmp_path, esp=esp,
                        run=lambda *args: json.dumps({"filesystems": [
                            {"target": str(esp), "fstype": "autofs"}]}))
    with pytest.raises(r.RecoveryError, match="not mounted"):
        helper.esp_mount()


def test_no_matching_uki_refuses_checkpoint(image):
    helper, _, _, esp, *_ = image
    (esp / "EFI/Linux/exo-os.efi").write_bytes(uki(kernel="missing"))
    with pytest.raises(r.RecoveryError, match="exactly one"):
        helper.checkpoint()


def test_uki_companions_refused(image):
    helper, _, _, esp, *_ = image
    (esp / "EFI/Linux/exo-os.efi.extra.d").mkdir()
    with pytest.raises(r.RecoveryError, match="companion"):
        helper.checkpoint()


def test_missing_or_enabled_secure_boot_refused(monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"\0\0\0\0\1")
    with pytest.raises(r.RecoveryError, match="Secure Boot disabled"):
        r.firmware_check()
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"")
    with pytest.raises(r.RecoveryError):
        r.firmware_check()


def test_administrator_required(monkeypatch, capsys):
    monkeypatch.setattr(r.sys, "platform", "linux")
    monkeypatch.setattr(r.os, "geteuid", lambda: 1000)
    assert r.main(["checkpoint"]) == 1
    assert "root administrator" in capsys.readouterr().err


def test_failed_boot_copy_never_publishes_menu(image, monkeypatch):
    helper, _, _, esp, *_ = image
    checkpoint = helper.checkpoint()
    original = Path.open
    def fail(path, *args, **kwargs):
        if "exo-recovery" in str(path) and path.suffix == ".efi" and "xb" in args:
            raise OSError("ESP full")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="ESP full"):
        helper.stage(checkpoint["id"])
    assert not list((esp / "loader/entries").glob("*.conf"))


def test_writable_checkpoint_parent_rejected(image):
    helper, _, snapshots, *_ = image
    checkpoint = helper.checkpoint()
    (snapshots / "exo-recovery/checkpoints").chmod(0o777)
    with pytest.raises(r.RecoveryError, match="not writable"):
        helper.stage(checkpoint["id"])


def test_snapshot_fstab_symlink_rejected(image, tmp_path):
    helper, _, snapshots, *_ = image
    checkpoint = helper.checkpoint()
    fstab = snapshots / "exo-recovery/checkpoints" / checkpoint["id"] / "root/etc/fstab"
    outside = tmp_path / "fstab"
    fstab.rename(outside)
    fstab.symlink_to(outside)
    with pytest.raises(r.RecoveryError, match="Unsafe path"):
        helper.stage(checkpoint["id"])


def test_recovery_does_not_expand_model_root_actions():
    from exo_os.sysd.protocol import NAMED_ACTIONS
    from exo_os.plugin import TOOLS
    assert NAMED_ACTIONS == {"status", "snapshot.create", "snapshot.list", "snapshot.undo", "package.install"}
    assert not any("recovery" in tool[0] or "boot" in tool[0] for tool in TOOLS)


def test_image_exposes_manual_entrypoint_and_regular_uki_default():
    project = Path(__file__).resolve().parents[1]
    config = (project / "image/mkosi.extra/efi/loader/loader.conf").read_text()
    assert "default *.efi" in config and "timeout 5" in config and "editor no" in config
    assert "exo-os-recovery" in (project / "image/mkosi.postinst.chroot").read_text()


def test_staging_removes_captured_pacman_lock_only_from_clone(image):
    helper, root, snapshots, *_ = image
    lock = root / "var/lib/pacman/db.lck"
    lock.parent.mkdir(parents=True)
    lock.write_text("live administrator lock")
    lock.chmod(0o600)
    checkpoint = helper.checkpoint()
    saved = snapshots / "exo-recovery/checkpoints" / checkpoint["id"] / "root/var/lib/pacman/db.lck"
    assert saved.read_text() == "live administrator lock"
    helper.stage(checkpoint["id"])
    cloned = snapshots / "exo-recovery/roots" / checkpoint["id"] / "var/lib/pacman/db.lck"
    assert not cloned.exists()
    assert lock.read_text() == "live administrator lock"
    assert saved.read_text() == "live administrator lock"


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_staging_refuses_unsafe_captured_pacman_lock(image, tmp_path, kind):
    helper, root, _, esp, *_ = image
    lock = root / "var/lib/pacman/db.lck"
    lock.parent.mkdir(parents=True)
    outside = tmp_path / "outside-lock"
    outside.write_text("must survive")
    if kind == "symlink":
        lock.symlink_to(outside)
    else:
        lock.mkdir()
    checkpoint = helper.checkpoint()
    with pytest.raises(r.RecoveryError, match="Unsafe path|regular file"):
        helper.stage(checkpoint["id"])
    assert outside.read_text() == "must survive"
    assert not list((esp / "loader/entries").glob("*.conf"))


def test_persistent_helper_identity_and_new_audit_excluded_from_system_snapshot(image):
    helper, root, snapshots, *_ = image
    checkpoint = helper.checkpoint()
    state = root / "var/lib/exo-sysd"
    (state / "sysd.key").write_text("newer-fixture-key")
    (state / "audit.log").write_text("old root receipt\npost-checkpoint root receipt")
    before = {name: hashlib.sha256((state / name).read_bytes()).hexdigest() for name in ("sysd.key", "audit.log")}
    helper.stage(checkpoint["id"])
    for name in before:
        assert hashlib.sha256((state / name).read_bytes()).hexdigest() == before[name]
        for directory in ("checkpoints", "roots"):
            location = snapshots / "exo-recovery" / directory / checkpoint["id"]
            if directory == "checkpoints":
                location /= "root"
            assert not (location / "var/lib/exo-sysd" / name).exists()


def test_missing_helper_fstab_mount_rejects_old_image_checkpoint(image):
    helper, root, *_ = image
    (root / "etc/fstab").write_text("\n".join(line for line in FSTAB.splitlines() if "exo-sysd" not in line))
    with pytest.raises(r.RecoveryError, match="helper"):
        helper.checkpoint()


def test_wrong_helper_subvolume_rejected_before_snapshot(image):
    helper, root, *_, calls, _ = image
    run = helper.run
    def wrong_mount(*args):
        response = run(*args)
        if args[0] == "findmnt" and str(root / "var/lib/exo-sysd") in args:
            value = json.loads(response)
            value["filesystems"][0]["fsroot"] = "/@"
            return json.dumps(value)
        return response
    helper.run = wrong_mount
    with pytest.raises(r.RecoveryError, match="Unexpected image mount"):
        helper.checkpoint()
    assert not any(args[:3] == ("btrfs", "subvolume", "snapshot") for args in calls)


def test_helper_state_is_persistently_mounted_and_unit_fails_without_mount():
    from exo_os.sysd.server import KEY, LOG
    project = Path(__file__).resolve().parents[1]
    assert KEY.parent == LOG.parent == Path("/var/lib/exo-sysd")
    fstab = (project / "image/mkosi.extra/etc/fstab").read_text()
    assert "/var/lib/exo-sysd btrfs subvol=@sysd" in fstab
    repart = (project / "image/mkosi.repart/10-root.conf").read_text()
    assert "CopyFiles=/var/lib/exo-sysd:/@sysd" in repart
    assert "/@/var/lib/exo-sysd/" in repart
    unit = (project / "image/mkosi.extra/usr/lib/systemd/system/exo-sysd.service").read_text()
    assert "RequiresMountsFor=/var/lib/exo-sysd" in unit
    assert "AssertPathIsMountPoint=/var/lib/exo-sysd" in unit
    assert "StateDirectoryMode=0700" in unit


@pytest.mark.parametrize("replacement", [None, "@", "@store"])
def test_egress_fstab_omission_or_wrong_subvolume_refused(image, replacement):
    helper, root, *_, calls, _ = image
    lines = []
    for line in FSTAB.splitlines():
        if "exo-egress" in line:
            if replacement is None:
                continue
            line = line.replace("subvol=@egress", "subvol=" + replacement)
        lines.append(line)
    (root / "etc/fstab").write_text("\n".join(lines))
    with pytest.raises(r.RecoveryError):
        helper.checkpoint()
    assert not any(args[:3] == ("btrfs", "subvolume", "snapshot") for args in calls)


@pytest.mark.parametrize("field,value", [("fsroot", "/@"), ("uuid", SNAP_UUID), ("fstype", "ext4")])
def test_egress_mount_identity_mismatch_refused(image, field, value):
    helper, root, *_, calls, _ = image
    run = helper.run
    def wrong_mount(*args):
        response = run(*args)
        if args[0] == "findmnt" and str(root / "var/lib/exo-egress") in args:
            data = json.loads(response)
            data["filesystems"][0][field] = value
            return json.dumps(data)
        return response
    helper.run = wrong_mount
    with pytest.raises(r.RecoveryError):
        helper.checkpoint()
    assert not any(args[:3] == ("btrfs", "subvolume", "snapshot") for args in calls)


def test_post_checkpoint_reserved_egress_operation_survives_staging_without_retry(image):
    from exo_os.egress.broker import Broker, Journal
    from exo_os.sysd.protocol import encode
    helper, root, snapshots, *_ = image
    state = root / "var/lib/exo-egress"
    journal = Journal(state / "requests.sqlite")
    checkpoint = helper.checkpoint()
    request = {"version": 1, "nonce": "recovery-test", "request_id": "reserved-after-checkpoint",
               "profile": "recovery-fixture", "method": "POST", "expected_origin": "https://fixture.invalid",
               "path": "/recovery-probe", "body": ""}
    fingerprint = hashlib.sha256(encode({key: value for key, value in request.items()
                                       if key not in {"nonce", "version"}})).hexdigest()
    assert journal.reserve(request["request_id"], fingerprint) is None
    helper.stage(checkpoint["id"])
    restarted = Journal(state / "requests.sqlite")
    with restarted.connect() as db:
        assert db.execute("SELECT id,fingerprint,outcome FROM requests").fetchall() == [
            (request["request_id"], fingerprint, None)]
    broker = Broker({}, restarted, state)
    reply = broker.handle(request)
    assert reply["state"] == "unknown" and reply["retryable"] is False
    with restarted.connect() as db:
        assert db.execute("SELECT id,fingerprint,outcome FROM requests").fetchall() == [
            (request["request_id"], fingerprint, None)]
    for directory in ("checkpoints", "roots"):
        location = snapshots / "exo-recovery" / directory / checkpoint["id"]
        if directory == "checkpoints":
            location /= "root"
        assert not (location / "var/lib/exo-egress/requests.sqlite").exists()


def test_stage_refuses_checkpoint_missing_egress_mount_contract(image):
    helper, _, snapshots, *_, calls, _ = image
    checkpoint = helper.checkpoint()
    fstab = snapshots / "exo-recovery/checkpoints" / checkpoint["id"] / "root/etc/fstab"
    fstab.write_text("\n".join(line for line in FSTAB.splitlines() if "exo-egress" not in line))
    calls.clear()
    with pytest.raises(r.RecoveryError, match="egress"):
        helper.stage(checkpoint["id"])
    assert not any(args[:3] == ("btrfs", "subvolume", "snapshot") for args in calls)


def lock_env(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    return tmp_path / "db.lck", tmp_path / "recovery.lock", proc


def fake_process(proc, pid, start="100", comm="bash", cmdline=b"bash\0"):
    path = proc / str(pid)
    path.mkdir()
    (path / "stat").write_text(f"{pid} ({comm} x) S " + " ".join(["0"] * 18 + [start, "0"]))
    (path / "comm").write_text(comm + "\n")
    (path / "cmdline").write_bytes(cmdline)


def test_administrator_lock_records_holder_and_releases(tmp_path):
    db, run, proc = lock_env(tmp_path)
    fake_process(proc, os.getpid(), start="555")
    with r.administrator_lock(db, run, proc):
        assert db.read_text() == f"{os.getpid()} 555\n"
    assert not db.exists()


def test_stale_lock_from_dead_holder_is_taken_over(tmp_path):
    db, run, proc = lock_env(tmp_path)
    db.write_text("4242 100\n")
    with r.administrator_lock(db, run, proc):
        assert db.read_text().split()[0] == str(os.getpid())
    assert not db.exists()


def test_live_holder_is_refused_and_lock_path_named(tmp_path):
    db, run, proc = lock_env(tmp_path)
    fake_process(proc, 4242, start="100")
    db.write_text("4242 100\n")
    with pytest.raises(r.RecoveryError, match=str(db)):
        with r.administrator_lock(db, run, proc):
            pass
    assert db.read_text() == "4242 100\n"


def test_reused_pid_is_not_a_live_holder(tmp_path):
    db, run, proc = lock_env(tmp_path)
    fake_process(proc, 4242, start="999")
    db.write_text("4242 100\n")
    with r.administrator_lock(db, run, proc):
        assert db.read_text().split()[0] == str(os.getpid())


@pytest.mark.parametrize("content", ["", "not a holder", "12", "1 2 3", "-5 7", "x y"])
def test_empty_or_garbage_lock_taken_over_when_nothing_runs(tmp_path, content):
    db, run, proc = lock_env(tmp_path)
    db.write_text(content)
    with r.administrator_lock(db, run, proc):
        assert db.read_text().split()[0] == str(os.getpid())


@pytest.mark.parametrize("content", ["", "garbage"])
@pytest.mark.parametrize("comm,cmdline", [("pacman", b"pacman\0-Syu\0"),
                                          ("python3", b"python3\0-m\0exo_os.recovery\0checkpoint\0")])
def test_empty_or_garbage_lock_refused_while_pacman_or_recovery_runs(tmp_path, content, comm, cmdline):
    db, run, proc = lock_env(tmp_path)
    fake_process(proc, 777, comm=comm, cmdline=cmdline)
    db.write_text(content)
    with pytest.raises(r.RecoveryError, match=str(db)):
        with r.administrator_lock(db, run, proc):
            pass
    assert db.read_text() == content


def test_symlink_lock_is_never_taken_over(tmp_path):
    db, run, proc = lock_env(tmp_path)
    target = tmp_path / "target"
    target.write_text("")
    db.symlink_to(target)
    with pytest.raises(r.RecoveryError, match=str(db)):
        with r.administrator_lock(db, run, proc):
            pass
    assert target.exists()


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs Linux /proc")
def test_process_start_reads_real_proc():
    assert r.process_start(os.getpid()).isdigit()
    assert r.process_start(2**22 + 12345) is None

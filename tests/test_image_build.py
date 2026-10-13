"""Build-time guards in the image sources and tools. Nothing here builds or boots an image."""
import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
OVERLAY = PROJECT / "image/mkosi.extra"


def sh(script, *, env=None):
    base = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "EXO_OS_TOOLS")}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={**base, **(env or {})})


@pytest.mark.parametrize("path", ["tools/build-image.sh", "tools/env.sh", "tools/run-vm.sh",
                                  "image/mkosi.postinst.chroot"])
def test_scripts_parse(path):
    assert subprocess.run(["bash" if path != "image/mkosi.postinst.chroot" else "sh", "-n", str(PROJECT / path)]).returncode == 0


def test_wheelhouse_python_is_one_value_checked_in_the_image():
    build = (PROJECT / "tools/build-image.sh").read_text()
    assert not re.search(r"--python-version\s+3\.\d+", build)
    assert '--python-version "$guest_python"' in build
    assert '> "$extra/opt/exo/GUEST_PYTHON"' in build
    post = (PROJECT / "image/mkosi.postinst.chroot").read_text()
    check = post.index("/opt/exo/GUEST_PYTHON")
    # The version check must run before anything installs from the wheelhouse.
    assert check < post.index("pip --python /opt/exo/bin/python install")
    assert "exit 1" in post[check:post.index("pip --python")]


def test_guest_python_override_is_validated():
    build = (PROJECT / "tools/build-image.sh").read_text()
    pattern = re.search(r"\[\[ \$guest_python =~ (\S+) \]\]", build).group(1)
    assert re.fullmatch(pattern, "3.15") and not re.fullmatch(pattern, "3.14; rm")


def test_mirrorlist_has_a_fallback():
    servers = [line for line in (OVERLAY / "etc/pacman.d/mirrorlist").read_text().splitlines()
               if line.startswith("Server")]
    assert len(servers) >= 2 and len(set(servers)) == len(servers)
    assert all(re.fullmatch(r"Server = https://\S+/\$repo/os/\$arch", s) for s in servers)


def fstab_mounts():
    mounts = {}
    for line in (OVERLAY / "etc/fstab").read_text().splitlines():
        fields = line.split()
        if fields and not fields[0].startswith("#"):
            mounts[fields[1]] = fields[3].split(",")
    return mounts


def test_required_mounts_stop_boot_and_helper_mounts_do_not():
    mounts = fstab_mounts()
    assert set(mounts) == {"/home", "/var/lib/exo", "/.snapshots", "/var/lib/exo-sysd",
                           "/var/lib/exo-egress", "/var/lib/exo-ledger"}
    # The account and the store are used at boot with no mount check of their own, and package
    # installs need @snapshots for their snap-pac pair (snap-pac does not abort pacman on failure).
    for required in ("/home", "/var/lib/exo", "/.snapshots"):
        assert "nofail" not in mounts[required]
    for helper in ("/var/lib/exo-sysd", "/var/lib/exo-egress", "/var/lib/exo-ledger"):
        assert "nofail" in mounts[helper]


def test_nofail_helper_mounts_are_required_by_their_services():
    units = "\n".join(p.read_text() for p in (OVERLAY / "usr/lib/systemd/system").glob("*.service"))
    for mount in ("/var/lib/exo-sysd", "/var/lib/exo-egress", "/var/lib/exo-ledger"):
        assert f"RequiresMountsFor={mount}" in units or re.search(rf"RequiresMountsFor=.*{mount}\b", units)
        assert f"AssertPathIsMountPoint={mount}" in units


def test_environment_d_does_not_depend_on_user():
    env = (OVERLAY / "usr/lib/environment.d/50-exo-os.conf").read_text()
    assert "${" not in env
    assert "EXO_HOME=/var/lib/exo/exo\n" in env and "EXO_PRINCIPAL=exo\n" in env
    helper = (PROJECT / "image/mkosi.profiles/vmtest/mkosi.extra/usr/local/bin/as-exo").read_text()
    assert "EXO_HOME=/var/lib/exo/exo " in helper and "EXO_PRINCIPAL=exo " in helper


def source_env(tmp_path, *versions):
    root = tmp_path / "root"
    dirs = []
    for v in versions:
        d = root / f"usr/lib/python{v}/site-packages"
        d.mkdir(parents=True)
        dirs.append(str(d))
    result = sh(f'set -eu; source "{PROJECT}/tools/env.sh"; echo "${{PYTHONPATH-unset}}"',
                env={"EXO_OS_TOOLS": str(tmp_path)})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip(), dirs


def test_env_leaves_pythonpath_alone_without_a_tools_python(tmp_path):
    assert source_env(tmp_path)[0] == "unset"


def test_env_adds_the_one_site_packages_directory(tmp_path):
    got, dirs = source_env(tmp_path, "3.14")
    assert got == dirs[0]


def test_env_joins_several_with_colons(tmp_path):
    got, dirs = source_env(tmp_path, "3.13", "3.14")
    assert got == ":".join(dirs) and " " not in got


def test_env_keeps_an_existing_pythonpath(tmp_path):
    (tmp_path / "root/usr/lib/python3.14/site-packages").mkdir(parents=True)
    result = sh(f'set -eu; source "{PROJECT}/tools/env.sh"; echo "$PYTHONPATH"',
                env={"EXO_OS_TOOLS": str(tmp_path), "PYTHONPATH": "/keep"})
    assert result.stdout.strip() == f"{tmp_path}/root/usr/lib/python3.14/site-packages:/keep"


@pytest.mark.parametrize("args", [["--mem"], ["--mem", "--gui"], ["--mem", ""], ["--mem", "0"], ["--mem", "2g"],
                                  ["--mem", "-1"]])
def test_run_vm_rejects_a_bad_memory_value(args, tmp_path):
    result = subprocess.run(["bash", str(PROJECT / "tools/run-vm.sh"), *args], capture_output=True, text=True,
                            env={**os.environ, "EXO_OS_TOOLS": str(tmp_path)})
    assert result.returncode == 2
    assert "--mem needs a number of megabytes" in result.stderr

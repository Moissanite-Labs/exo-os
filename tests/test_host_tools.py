"""tools/host-tools.sh with stubbed pacman and curl and real gpg signatures. Nothing here touches the network,
the host's package database or the Arch keyring."""
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/host-tools.sh"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("gpg", "gpgv", "bsdtar", "curl", "bash")),
    reason="needs gpg, gpgv, bsdtar, curl and bash")

PACMAN = """#!/bin/sh
[ -z "$STUB_PACMAN_FAIL" ] || exit 1
echo "$@" > "$STUB_PACMAN_ARGS"
# A real pacman prints file:// for a package found in a cache directory; ours is told to use an empty one.
cachedir=
while [ $# -gt 0 ]; do [ "$1" = --cachedir ] && cachedir=$2; shift; done
if [ -n "$cachedir" ] && [ -z "$(ls -A "$cachedir")" ]; then cat "$STUB_URLS"; else sed 's|^https://[^/]*/pool|file:///var/cache/pacman/pkg|' "$STUB_URLS"; fi
"""
# Serves $STUB_SERVE/<basename of url>; STUB_PARTIAL names a file to cut short, as a dropped connection does.
CURL = """#!/bin/bash
while [ $# -gt 0 ]; do
  case $1 in -o) out=$2; shift ;; --proto|--proto-redir|--retry) shift ;; -*) ;; *) url=$1 ;; esac
  shift
done
echo "$url" >> "$STUB_CALLS"
name=${url##*/}
[ -f "$STUB_SERVE/$name" ] || exit 22
if [ "${STUB_PARTIAL:-}" = "$name" ]; then
  head -c 40 "$STUB_SERVE/$name" > "$out"
  exit 18
fi
cp "$STUB_SERVE/$name" "$out"
"""


def run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class Signer:
    """A throwaway signing key, kept apart from the host's keyrings."""

    def __init__(self, home, name):
        self.home = home
        home.mkdir(mode=0o700)
        run(["gpg", "--homedir", str(home), "--batch", "--passphrase", "", "--quick-generate-key", name,
             "ed25519", "sign", "never"], check=True)
        out = run(["gpg", "--homedir", str(home), "--batch", "--with-colons", "--fingerprint"], check=True).stdout
        self.fingerprint = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))

    def export(self, path):
        path.write_bytes(subprocess.run(["gpg", "--homedir", str(self.home), "--batch", "--export"],
                                        capture_output=True, check=True).stdout)

    def sign(self, path):
        sig = Path(f"{path}.sig")
        sig.unlink(missing_ok=True)
        run(["gpg", "--homedir", str(self.home), "--batch", "--detach-sign", "-o", str(sig), str(path)], check=True)


@pytest.fixture(scope="module")
def keys():
    # gpg-agent puts a socket in the key home, so the path has to be short (as in conftest.socket_dir).
    with tempfile.TemporaryDirectory(prefix="exo-gpg-", dir="/tmp") as tmp:
        base = Path(tmp)
        signers = Signer(base / "packager", "Test Packager"), Signer(base / "stranger", "Test Stranger")
        yield signers
        for signer in signers:
            subprocess.run(["gpgconf", "--homedir", str(signer.home), "--kill", "all"], capture_output=True)


def package(path, files):
    """Write a small .pkg.tar.xz holding {name: text}."""
    with tarfile.open(path, "w:xz") as tar:
        for name, text in {".PKGINFO": "pkgname = x\n", **files}.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.startswith("usr/bin/") else 0o644
            tar.addfile(info, io.BytesIO(data))


class Host:
    """A prefix, a mirror directory and stub commands for one test."""

    def __init__(self, tmp_path, keys):
        self.root = tmp_path
        self.signer, self.stranger = keys
        self.prefix = tmp_path / "prefix"
        self.serve = tmp_path / "mirror"
        self.keyrings = tmp_path / "keyrings"
        self.stubs = tmp_path / "stubs"
        self.calls = tmp_path / "calls"
        self.urls = tmp_path / "urls"
        for d in (self.serve, self.keyrings, self.stubs):
            d.mkdir()
        self.calls.touch()
        (self.stubs / "pacman").write_text(PACMAN)
        (self.stubs / "curl").write_text(CURL)
        for stub in self.stubs.iterdir():
            stub.chmod(0o755)
        self.signer.export(self.keyrings / "archlinux.gpg")
        (self.keyrings / "archlinux-revoked").write_text("0" * 40 + "\n")
        self.env = {
            **os.environ, "HOME": str(tmp_path / "home"), "EXO_OS_TOOLS": str(self.prefix),
            "EXO_OS_KEYRINGS": str(self.keyrings), "PATH": f"{self.stubs}:{os.environ['PATH']}",
            "STUB_URLS": str(self.urls), "STUB_PACMAN_ARGS": str(tmp_path / "pacman-args"), "STUB_SERVE": str(self.serve), "STUB_CALLS": str(self.calls),
        }
        self.env.pop("PYTHONPATH", None)
        self.listed = []

    def mirror(self, name, files, *, signer=None, tamper=False, scheme="https"):
        path = self.serve / name
        package(path, files)
        (signer or self.signer).sign(path)
        if tamper:
            package(path, {**files, "usr/bin/evil": "x"})
        self.listed.append(f"{scheme}://mirror.example/pool/{name}")

    def pefile(self, minor, name="python-pefile-1-1-any.pkg.tar.xz", **extra):
        site = f"usr/lib/python{minor}/site-packages"
        self.mirror(name, {f"{site}/pefile.py": "# pefile\n", f"{site}/peutils.py": "# peutils\n",
                           f"{site}/ordlookup/__init__.py": "# ordlookup\n"}, **extra)

    def tools(self, minor="3.14", release=1, **extra):
        """The package set host-tools.sh needs: ukify and pefile under one Python."""
        self.mirror(f"systemd-ukify-1-{release}-any.pkg.tar.xz", {"usr/bin/ukify": "#!/usr/bin/python3\n"}, **extra)
        self.pefile(minor, f"python-pefile-1-{release}-any.pkg.tar.xz", **extra)

    def run(self, **env):
        self.urls.write_text("\n".join(self.listed) + "\n")
        return run(["bash", str(SCRIPT)], env={**self.env, **{k: str(v) for k, v in env.items()}})

    @property
    def fetched(self):
        return self.calls.read_text().split()

    def leftovers(self):
        return sorted(p.name for p in (self.prefix / "cache").glob("*.part"))


@pytest.fixture
def host(tmp_path, keys):
    return Host(tmp_path, keys)


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck is not installed")
def test_script_is_shellcheck_clean():
    assert subprocess.run(["shellcheck", str(SCRIPT)]).returncode == 0


def test_verified_packages_are_installed(host):
    host.tools()
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert (host.prefix / "root/usr/bin/ukify").is_file()
    assert (host.prefix / "bin/ukify").is_file() and (host.prefix / "bin/pefile.py").is_file()
    assert (host.prefix / "bin/ordlookup/__init__.py").is_file()
    # Metadata files are not unpacked, and every package kept its signature next to it in the cache.
    assert not (host.prefix / "root/.PKGINFO").exists()
    cache = sorted(p.name for p in (host.prefix / "cache").iterdir())
    assert cache == sorted(f"{n}{s}" for n in ("python-pefile-1-1-any.pkg.tar.xz", "systemd-ukify-1-1-any.pkg.tar.xz")
                           for s in ("", ".sig"))
    pth = next((host.prefix / "venv").glob("lib/python3*/site-packages/exo-os-tools.pth"))
    assert pth.read_text().strip() == str(host.prefix / "root/usr/lib/python3.14/site-packages")
    assert "installed 2 packages" in result.stdout


@pytest.mark.parametrize("scheme", ["http", "file", "ftp"])
def test_non_https_urls_are_refused(host, scheme):
    host.tools()
    host.listed[0] = host.listed[0].replace("https", scheme, 1)
    result = host.run()
    assert result.returncode != 0 and "refusing" in result.stderr
    assert host.fetched == [] and not (host.prefix / "root").exists()


@pytest.mark.parametrize("url", ["https://mirror.example/pool/x.pkg.tar.xz?mirror=other",
                                 "https://mirror.example/pool/../x.tar", "https://mirror.example/"])
def test_odd_urls_are_refused(host, url):
    host.tools()
    host.listed.append(url)
    result = host.run()
    assert result.returncode != 0 and "refusing" in result.stderr
    assert host.fetched == [] and not (host.prefix / "root").exists()


def test_a_package_changed_after_signing_is_not_unpacked(host):
    host.tools()
    host.mirror("mkosi-1-1-any.pkg.tar.xz", {"usr/bin/mkosi": "ok\n"}, tamper=True)
    result = host.run()
    assert result.returncode != 0 and "signature check failed" in result.stderr
    assert not (host.prefix / "root").exists() and not (host.prefix / "bin").exists()
    # Nothing unverified is left under the name that later runs trust.
    assert not (host.prefix / "cache/mkosi-1-1-any.pkg.tar.xz").exists() and host.leftovers() == []


def test_a_missing_signature_stops_the_run(host):
    host.tools()
    (host.serve / "python-pefile-1-1-any.pkg.tar.xz.sig").unlink()
    result = host.run()
    assert result.returncode != 0 and "download" in result.stderr and "failed" in result.stderr
    assert not (host.prefix / "root").exists() and host.leftovers() == []


def test_a_key_outside_the_keyring_is_refused(host):
    host.tools(signer=host.stranger)
    result = host.run()
    assert result.returncode != 0 and "signature check failed" in result.stderr
    assert not (host.prefix / "root").exists()


def test_a_revoked_key_is_refused(host):
    host.tools()
    (host.keyrings / "archlinux-revoked").write_text(f"# revoked\n{host.signer.fingerprint.lower()}\n")
    result = host.run()
    assert result.returncode != 0 and "signature check failed" in result.stderr
    assert not (host.prefix / "root").exists()


@pytest.mark.parametrize("missing", ["archlinux.gpg", "archlinux-revoked"])
def test_a_missing_keyring_fails_closed(host, missing):
    host.tools()
    (host.keyrings / missing).unlink()
    result = host.run()
    assert result.returncode != 0 and "keyring" in result.stderr
    assert host.fetched == []


def test_one_bad_package_stops_the_run_before_anything_is_unpacked(host):
    host.tools()
    host.mirror("zzz-last-1-1-any.pkg.tar.xz", {"usr/bin/zzz": "x\n"}, signer=host.stranger)
    result = host.run()
    assert result.returncode != 0
    assert not (host.prefix / "root").exists()


def test_a_cached_file_from_an_older_run_is_verified_again(host):
    host.tools()
    cache = host.prefix / "cache"
    cache.mkdir(parents=True)
    # An older version of the script cached whatever it downloaded, with no signature.
    package(cache / "systemd-ukify-1-1-any.pkg.tar.xz", {"usr/bin/ukify": "evil\n"})
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert (host.prefix / "root/usr/bin/ukify").read_text() == "#!/usr/bin/python3\n"
    assert any(u.endswith("systemd-ukify-1-1-any.pkg.tar.xz") for u in host.fetched)


def test_a_cached_verified_file_is_not_downloaded_again(host):
    host.tools()
    assert host.run().returncode == 0
    host.calls.write_text("")
    assert host.run().returncode == 0
    assert host.fetched == []


def test_a_cut_short_download_does_not_poison_later_runs(host):
    host.tools()
    result = host.run(STUB_PARTIAL="python-pefile-1-1-any.pkg.tar.xz")
    assert result.returncode != 0 and "download" in result.stderr and "failed" in result.stderr
    assert not (host.prefix / "cache/python-pefile-1-1-any.pkg.tar.xz").exists() and host.leftovers() == []
    # The next run, with a working connection, starts that download again and succeeds.
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert (host.prefix / "bin/pefile.py").is_file()


def test_a_cut_short_signature_download_is_not_kept(host):
    host.tools()
    result = host.run(STUB_PARTIAL="python-pefile-1-1-any.pkg.tar.xz.sig")
    assert result.returncode != 0
    assert not (host.prefix / "cache/python-pefile-1-1-any.pkg.tar.xz").exists() and host.leftovers() == []
    assert host.run().returncode == 0


def test_a_pacman_failure_is_not_taken_for_nothing_to_do(host):
    host.tools()
    result = host.run(STUB_PACMAN_FAIL="1")
    assert result.returncode != 0 and not (host.prefix / "root").exists()


def test_nothing_to_fetch_leaves_the_prefix_alone(host):
    host.tools()
    assert host.run().returncode == 0
    host.listed.clear()
    result = host.run()
    assert result.returncode == 0 and "nothing to fetch" in result.stdout
    assert (host.prefix / "bin/ukify").is_file()


def python_minor():
    return run(["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], env=os.environ).stdout.strip()


def test_a_python_minor_bump_rebuilds_root_and_venv(host):
    host.tools(minor="3.13")
    assert host.run().returncode == 0
    old_venv = host.prefix / "venv/lib/python3.99/site-packages"
    old_venv.mkdir(parents=True)
    (old_venv / "exo-os-tools.pth").write_text("/old\n")
    # Arch moves python: the same package names now carry the new minor version's directory.
    host.listed.clear()
    host.tools(minor="3.14", release=2)
    result = host.run()
    assert result.returncode == 0, result.stderr
    libs = sorted(p.name for p in (host.prefix / "root/usr/lib").iterdir())
    assert libs == ["python3.14"]
    pths = list((host.prefix / "venv").glob("lib/python*/site-packages/exo-os-tools.pth"))
    assert [p.read_text().strip() for p in pths] == [str(host.prefix / "root/usr/lib/python3.14/site-packages")]


def test_one_site_packages_is_chosen_when_several_exist(host):
    # A package set where another package ships a different Python's directory (and the lower version name
    # sorts after the higher one as text): pefile's directory is still the one used.
    host.tools(minor="3.9")
    host.mirror("other-1-1-any.pkg.tar.xz", {"usr/lib/python3.14/site-packages/other.py": "x\n",
                                             "usr/lib/python3.10/site-packages/other2.py": "x\n"})
    result = host.run()
    assert result.returncode == 0, result.stderr
    pth = next((host.prefix / "venv").glob("lib/python3*/site-packages/exo-os-tools.pth"))
    assert pth.read_text().strip() == str(host.prefix / "root/usr/lib/python3.9/site-packages")


def test_the_hosts_python_minor_wins_among_several_pefile_directories(host):
    minor = python_minor()
    host.tools(minor="3.99")
    host.pefile(minor, "pefile-too-1-1-any.pkg.tar.xz")
    result = host.run()
    assert result.returncode == 0, result.stderr
    pth = next((host.prefix / "venv").glob("lib/python3*/site-packages/exo-os-tools.pth"))
    assert pth.read_text().strip() == str(host.prefix / f"root/usr/lib/python{minor}/site-packages")


def test_a_failed_unpack_keeps_the_previous_install(host):
    host.tools()
    assert host.run().returncode == 0
    before = (host.prefix / "root/usr/bin/ukify").read_text()
    # Validly signed, but not an archive.
    broken = host.serve / "broken-1-1-any.pkg.tar.xz"
    broken.write_bytes(b"not an archive")
    host.signer.sign(broken)
    host.listed.append("https://mirror.example/pool/broken-1-1-any.pkg.tar.xz")
    result = host.run()
    assert result.returncode != 0
    assert (host.prefix / "root/usr/bin/ukify").read_text() == before


def test_a_pacman_list_without_pefile_is_reported(host):
    host.mirror("mkosi-1-1-any.pkg.tar.xz", {"usr/bin/mkosi": "x\n"})
    result = host.run()
    assert result.returncode != 0 and "python-pefile" in result.stderr


def test_pacman_is_pointed_at_an_empty_cache_so_it_prints_remote_urls(host):
    host.tools()
    result = host.run()
    assert result.returncode == 0, result.stderr
    args = (host.root / "pacman-args").read_text().split()
    assert args[:2] == ["-Sp", "--needed"] and "--cachedir" in args
    # The directory handed to pacman is not the host's cache, and it is empty (the stub prints file:// otherwise).
    assert args[args.index("--cachedir") + 1] != "/var/cache/pacman/pkg"
    assert all(u.startswith("https://") for u in host.fetched)

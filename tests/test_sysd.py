import json
import socket
import subprocess
import threading

import pytest

from exo_os.sysd.actions import ActionError, Actions
from exo_os.sysd.client import SysdClient, SysdError
from exo_os.sysd.server import Server


class FakeRunner:
    def __init__(self, snap_pac=True, repo=("htop", "foot")):
        self.calls, self.snap_pac, self.repo, self.n = [], snap_pac, set(repo), 10

    def __call__(self, argv):
        self.calls.append(argv)
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if argv[:2] == ["pacman", "-Q"]:
            return subprocess.CompletedProcess(argv, 0 if self.snap_pac else 1, "", "")
        if argv[:2] == ["pacman", "-Si"]:
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in self.repo else 1, "", "")
        if argv[0] == "snapper" and "create" in argv:
            self.n += 1
            return ok(f"{self.n}\n")
        if argv[0] == "snapper" and "list" in argv:
            return ok(json.dumps({"root": [{"number": 0}, {"number": 11, "description": "pre"},
                                           {"number": 12, "description": "post"}]}))
        return ok()


def test_package_install_with_snap_pac():
    r = FakeRunner()
    out = Actions(r).package_install({"names": ["htop"]})
    assert out == {"installed": ["htop"], "pre": 11, "post": 12}
    assert r.calls.index(["pacman", "-Sy", "--noconfirm"]) < r.calls.index(["pacman", "-Su", "--needed", "--noconfirm", "--", "htop"])
    assert not any("create" in c for c in r.calls)


def test_package_install_without_snap_pac_takes_own_snapshots():
    r = FakeRunner(snap_pac=False)
    out = Actions(r).package_install({"names": ["htop", "foot"]})
    assert out["pre"] == 11 and out["post"] == 12
    creates = [c for c in r.calls if "create" in c]
    assert len(creates) == 2 and "before installing htop foot" in creates[0]


@pytest.mark.parametrize("names", [[], ["-Syu"], ["htop; rm -rf /"], ["../x"], ["a"] * 11, "htop", [3]])
def test_package_names_are_checked(names):
    with pytest.raises(ActionError):
        Actions(FakeRunner()).package_install({"names": names})


def test_unknown_package_refused_before_pacman_runs():
    r = FakeRunner()
    with pytest.raises(ActionError, match="not in the package repositories: nope"):
        Actions(r).package_install({"names": ["htop", "nope"]})
    assert not any(c[:2] == ["pacman", "-Su"] for c in r.calls)


def test_snapshot_description_checked():
    with pytest.raises(ActionError):
        Actions(FakeRunner()).snapshot_create({"description": "x\n--rm"})


def _serve(tmp_path, runner):
    path = str(tmp_path / "sysd.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.listen(4)
    srv = Server(Actions(runner), key_path=tmp_path / "key", log_path=tmp_path / "log", allowed_gid=None)
    threading.Thread(target=srv.serve, args=(sock,), daemon=True).start()
    return path, srv


def test_server_runs_named_actions_and_signs_receipts(tmp_path, monkeypatch):
    from exo import crypto

    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    path, srv = _serve(tmp_path, FakeRunner())
    client = SysdClient(path)
    assert client.call("snapshot.create", {"description": "test"}) == {"snapshot": 11}
    with pytest.raises(SysdError, match="does not do 'shell'"):
        client.call("shell", {"cmd": "id"})
    with pytest.raises(SysdError, match="not a package name"):
        client.call("package.install", {"names": ["-Syu"]})
    receipts = [json.loads(line) for line in (tmp_path / "log").read_text().splitlines()]
    assert [r["action"] for r in receipts] == ["snapshot.create", "package.install"]
    assert all(crypto.verify_doc(r, r["pub"]) for r in receipts)
    tampered = dict(receipts[0], ok=False)
    assert not crypto.verify_doc(tampered, tampered["pub"])


def test_bad_requests(tmp_path):
    srv = Server(Actions(FakeRunner()), key_path=tmp_path / "key", log_path=None)
    assert srv.handle_line(b"not json", (1, 1000, 1000)) == {"ok": False, "error": "bad request"}
    assert srv.handle_line(b'{"action": "status", "args": []}', (1, 1000, 1000))["error"] == "bad request"


def test_unreachable_helper(tmp_path):
    with pytest.raises(SysdError, match="not reachable"):
        SysdClient(str(tmp_path / "none.sock")).call("status")

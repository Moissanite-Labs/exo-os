"""Internal unprivileged helpers; fixed admin launcher owns their input and identities."""
from __future__ import annotations

import ctypes
import os
import resource
import sys
from pathlib import Path

from .process import MAX_EXPORT, account
from .state import WitnessError, decode, encoded, validate_policy


def restrict(mode, parent_pid=None):
    if sys.platform != "linux":
        raise WitnessError("witness privilege isolation requires Linux")
    uid, gid = account("exo-ledger" if mode == "export" else "exo-witness")
    if os.geteuid() != uid or os.getuid() != uid or os.getegid() != gid or os.getgroups():
        raise WitnessError("witness helper has the wrong kernel identity/groups")
    libc = ctypes.CDLL(None, use_errno=True)
    parent_pid = os.getppid() if parent_pid is None else parent_pid
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(1, 9, 0, 0, 0):  # no-new-privs and parent-death SIGKILL
        raise WitnessError("witness helper privilege restriction failed")
    if parent_pid <= 1 or os.getppid() != parent_pid:
        raise WitnessError("witness helper parent is no longer available")
    for kind, amount in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64),
                         (resource.RLIMIT_CPU, 60), (resource.RLIMIT_AS, 512 * 1024 * 1024),
                         (resource.RLIMIT_FSIZE, MAX_EXPORT), (resource.RLIMIT_NPROC, 1)):
        _, hard = resource.getrlimit(kind)
        resource.setrlimit(kind, (min(amount, hard) if hard != resource.RLIM_INFINITY else amount,) * 2)
    if mode == "export":
        from ..sandbox.restrict import seccomp
        seccomp()  # network denied before any signer journal/key loader is imported


def export_prefix(journal, output):
    """One captured target, bounded checkpoint-only pages, never a moving-head chase."""
    from exo.ledger_authority import canonical
    target = journal.head()
    output.write(encoded({"target": target}))
    total = 0
    for start in range(0, target["seq"] + 1, 64):
        for seq in range(start, min(start + 64, target["seq"] + 1)):
            raw = canonical(journal.head(seq)) + b"\n"
            total += len(raw)
            if len(raw) > 8193 or total > MAX_EXPORT:
                raise WitnessError("checkpoint export exceeds fixed witness transport bounds")
            output.write(raw)


def export(request, output):
    from ..ledgerd.policy import load_manifest
    from ..ledgerd.server import load_journals
    policy = validate_policy(request["policy"])
    manifest = load_manifest()
    stream = policy["identity"]["stream_id"]
    if stream not in manifest["streams"] or manifest["streams"][stream]["identity"] != policy["identity"]:
        raise WitnessError("witness stream does not match protected authority")
    journal = load_journals(manifest, os.geteuid())[stream]
    if journal.head(0) != policy["genesis"]:
        raise WitnessError("witness genesis does not match protected authority")
    export_prefix(journal, output)


def network(request):
    import tempfile

    from exo.ledger_authority.witness_transport import WitnessClient

    from ..sandbox.restrict import landlock
    policy = validate_policy(request["policy"])
    path = Path(request["path"])
    tempfile.tempdir = str(path / "scratch")
    landlock(["/usr", "/opt/exo", "/proc", str(path)], [str(path / "scratch")])
    client = WitnessClient(policy["identity"], policy["genesis"], policy["witness_pub"],
                           **{key: policy[key] for key in ("host", "port", "server_hostname", "server_cert_sha256", "timeout")},
                           ca=path / "ca.pem", cert=path / "client.pem", tls_key=path / "client.key")
    receipt = client.publish(path / "checkpoints.jsonl") if request["publish"] else None
    return {"receipt": receipt, "head": client.query_head(request["nonce"])}


def main():
    try:
        if len(sys.argv) != 4 or sys.argv[1] not in ("export", "network"):
            raise WitnessError("invalid internal witness helper invocation")
        mode = sys.argv[1]
        restrict(mode, int(sys.argv[3]))
        with os.fdopen(int(sys.argv[2]), "rb") as source:
            raw = source.read(131073)
        if len(raw) > 131072:
            raise WitnessError("witness helper input exceeds limit")
        request = decode(raw)
        if mode == "export":
            export(request, sys.stdout.buffer)
        else:
            sys.stdout.buffer.write(encoded(network(request)))
        return 0
    except Exception:  # noqa: BLE001 - redact all failures at this credential-bearing process boundary
        # No credential paths/data in stderr, even for SSL/parser failures.
        print("witness helper refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

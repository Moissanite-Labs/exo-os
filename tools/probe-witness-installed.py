#!/usr/bin/env python3
"""Disposable VM acceptance driver. Imports runtime code only from /opt/exo.

The raw-image digest is a host-harness attestation, not guest-verified proof.
This driver deliberately leaves its persistent enrollment for overlay discard.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

RUNTIME = Path("/opt/exo")
PYTHON = str(RUNTIME / "bin/python")
ADMIN = str(RUNTIME / "bin/exo-witness-admin")
ENV_FIELDS = {
    "image_sha256": ("IMAGE_SHA256", 64),
    "source_sha256": ("SOURCE_SHA256", 64),
    "exo_pin": ("CORE_PIN", 40),
    "exo_os_commit": ("OS_COMMIT", 40),
}


def expected_environment(environment):
    if environment.get("EXO_WITNESS_LINUX_ACCEPTANCE") != "1":
        raise ValueError("explicit disposable Linux witness acceptance opt-in required")
    expected = {}
    for field, (name, length) in ENV_FIELDS.items():
        value = environment.get("EXO_WITNESS_EXPECTED_" + name, "")
        if not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value):
            raise ValueError("missing or invalid explicit witness expectation: " + name)
        expected[field] = value
    value = environment.get("EXO_WITNESS_EXPECTED_VM_UUID", "")
    if str(uuid.UUID(value)) != value:
        raise ValueError("canonical disposable VM UUID required")
    expected["vm_uuid"] = value
    return expected


def match_provenance(expected, manifest, pin, machine_uuid):
    if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int or manifest["schema"] != 2
            or manifest.get("build_profile") != "vmtest"
            or any(manifest.get(field) != expected[field]
                   for field in ("source_sha256", "exo_pin", "exo_os_commit"))
            or pin.strip() != expected["exo_pin"] or machine_uuid.strip().lower() != expected["vm_uuid"]):
        raise ValueError("installed vmtest provenance/core pin/disposable VM identity mismatch")


def refuse_enrollment(base, marker):
    # lexists also refuses dangling symlinks; never repair, delete or reuse state.
    if os.path.lexists(base) or os.path.lexists(marker):
        raise ValueError("preexisting or partial witness enrollment; discard this overlay")


def installed_origin(origin, runtime=RUNTIME):
    if not origin or not Path(origin).resolve(strict=True).is_relative_to(runtime.resolve(strict=True)):
        raise ValueError("runtime import did not resolve to the installed /opt/exo tree")
    return str(Path(origin).resolve(strict=True))


def command_json(argv, *, timeout, identity=None):
    """Use installed bounded pipe collection; redact child stderr and kill/reap on timeout."""
    from exo_os.witness.process import collect
    options = {}
    if identity is not None:
        uid, gid, groups = identity
        options = {"user": uid, "group": gid, "extra_groups": groups}
    process = subprocess.Popen(argv, cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               close_fds=True, start_new_session=True, **options)
    raw = collect(process, 65536, timeout=timeout)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("installed command returned no JSON object")
    return value


def cli(*arguments, timeout=45):
    return command_json([PYTHON, "-I", "-B", ADMIN, *arguments], timeout=timeout)


def preflight():
    expected = expected_environment(os.environ)
    if sys.platform != "linux" or os.geteuid() != 0 or not sys.flags.isolated:
        raise ValueError("requires disposable Linux root and installed Python -I")
    origins = {name: installed_origin(importlib.import_module(name).__file__) for name in
               ("exo", "exo_os", "exo_os.witness.admin", "exo_os.witness.worker")}
    from exo_os.sandbox.policy import trusted_path
    from exo_os.witness import state
    from exo_os.witness.process import identities
    identities()  # real static signer/publisher/desktop identities; no simulated ownership
    for origin in origins.values():
        trusted_path(Path(origin))
    trusted_path(Path(PYTHON).resolve(strict=True))
    trusted_path(Path(ADMIN))
    with state.directory(RUNTIME) as fd:
        manifest_raw = state.read_file(fd, "EXO_OS_BUILD.json", mode=0o644)
        pin = state.read_file(fd, "EXO_PIN", mode=0o644).decode()
    manifest = state.decode(manifest_raw)
    machine_uuid = Path("/sys/class/dmi/id/product_uuid").read_text()
    match_provenance(expected, manifest, pin, machine_uuid)
    with state.directory(state.BASE.parent):
        refuse_enrollment(state.BASE, state.BASE.parent / state.MARKER)
    assert cli("status") == {"state": "disabled", "fresh": False}
    return {"expected": expected, "installed_manifest": manifest, "runtime_imports": origins,
            "installed_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest()}


def public_authority():
    import grp
    import pwd
    from exo_os.ledgerd.policy import load_manifest
    manifest = load_manifest()  # protected public identity only; no journal/key open
    if len(manifest["streams"]) != 1:
        raise ValueError("installed acceptance requires exactly one provisioned stream")
    entry = next(iter(manifest["streams"].values()))
    desktop = pwd.getpwnam("exo")
    script = '''
import json, sys
from exo_os.ledgerd.client import ReadAuthorityClient, create_authority
reader = create_authority(sys.argv[1])
assert isinstance(reader, ReadAuthorityClient)
print(json.dumps({"identity": reader.identity, "genesis": reader.head(0), "head": reader.head()}))
'''
    public = command_json([PYTHON, "-I", "-B", "-c", script, entry["ledger_path"]], timeout=60,
                          identity=(desktop.pw_uid, desktop.pw_gid, [grp.getgrnam("exo-sys").gr_gid]))
    from exo.ledger_authority import verify_checkpoint
    assert public["identity"] == entry["identity"]
    for checkpoint in (public["genesis"], public["head"]):
        verify_checkpoint(public["identity"], checkpoint)
    assert public["genesis"]["seq"] == 0 and public["head"]["seq"] > 0, "boot authority must already have records"
    return public


def installed_flow(fixture):
    proof = preflight()  # recheck before creating witness state or enrollment
    public = public_authority()
    from exo import crypto
    from exo.ledger_authority import verify_checkpoint
    from exo.ledger_authority.witness import WitnessJournal, verify_receipt
    from exo.ledger_authority.witness_transport import WitnessServer, verify_head
    from exo_os.witness import state
    fixture = Path(fixture)
    if (not fixture.is_relative_to(Path("/run")) or not fixture.name.startswith("exo-witness-installed-")
            or fixture.name == "exo-witness-installed-"):
        raise ValueError("fresh private /run witness fixture required")
    with state.directory(fixture, mode=0o700) as fd:
        credentials = {name: state.read_file(fd, name) for name in
                       ("ca.pem", "client.pem", "client.key", "server.pem", "server.key")}
    key = crypto.keygen()  # synthetic witness key only, never an authority key
    witness = WitnessJournal.provision(fixture / "witness.sqlite", public["identity"], key, public["genesis"])
    import ssl
    server = WitnessServer(witness, public["genesis"], ca=fixture / "ca.pem", cert=fixture / "server.pem",
                           tls_key=fixture / "server.key", host="127.0.0.1", timeout=10,
                           client_cert_sha256=hashlib.sha256(ssl.PEM_cert_to_DER_cert(credentials["client.pem"].decode())).hexdigest())
    policy = {"version": 1, "identity": public["identity"], "genesis": public["genesis"],
              "witness_pub": crypto.pubkey_b64(key), "host": "127.0.0.1", "port": server.address[1],
              "server_hostname": "witness.test", "timeout": 10,
              "server_cert_sha256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(credentials["server.pem"].decode())).hexdigest()}
    bundle = fixture / "enrollment"
    bundle.mkdir(mode=0o700)
    with state.directory(bundle, mode=0o700) as fd:
        for name, raw in {"policy.json": state.encoded(policy), "receipt.json": state.encoded(witness.head()),
                          **{name: credentials[name] for name in state.CREDENTIALS}}.items():
            state.write_new(fd, name, raw)
        os.fsync(fd)
    # Observe signed public challenges on this synthetic server only. The real
    # signing function is delegated unchanged; installed admin/workers are separate
    # execs and receive no patched code or substitute export callbacks.
    signed_heads = []
    sign_doc = crypto.sign_doc
    def observe_signing(document, signing_key):
        signed = sign_doc(document, signing_key)
        if document.get("type") == "exo-ledger-witness-head":
            signed_heads.append(signed)
        return signed
    crypto.sign_doc = observe_signing
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        refuse_enrollment(state.BASE, state.BASE.parent / state.MARKER)
        assert cli("initialize", "--bundle", str(bundle))["state"] == "initialized"
        initialized = cli("status")
        assert initialized["state"] == "cached_receipt" and not initialized["fresh"]
        assert initialized["evidence"]["receipt"]["seq"] == 0
        published = cli("publish", timeout=190)  # actual signer export and separate Landlock network exec
        assert published["state"] == "published" and published["fresh"]
        checkpoint, receipt = published["authority_target"], published["receipt"]
        assert checkpoint["seq"] >= public["head"]["seq"] > 0
        verify_receipt(policy["identity"], checkpoint, receipt, policy["witness_pub"])
        assert witness.head() == receipt
        with state.established() as (fd, saved_policy, current, _):
            previous = state.validate_evidence(policy, state.decode(state.read_file(fd, "previous.json")))
            assert saved_policy == policy and current["receipt"] == receipt and current["checkpoint"] == checkpoint
            assert previous == initialized["evidence"]
        cached = cli("status")
        assert cached["state"] == "cached_receipt" and not cached["fresh"] and cached["evidence"] == current
        fresh = [cli("status", "--fresh", timeout=190) for _ in range(2)]
        for result in fresh:
            assert result["fresh"] and result["state"] in ("fresh_match_at_snapshot", "fresh_witness_behind")
            assert result["receipt"] == receipt
            verify_checkpoint(policy["identity"], result["authority_target"])
        assert cli("status")["evidence"] == current, "fresh status must not rewrite persistent evidence"
        assert len(signed_heads) == 4, "publish preflight/final challenge plus two fresh status challenges"
        nonces = [envelope["nonce"] for envelope in signed_heads]
        assert len(set(nonces)) == 4
        for envelope in signed_heads:
            verify_head(policy["identity"], policy["genesis"], envelope, policy["witness_pub"], envelope["nonce"])
        assert all(envelope["receipt"] == receipt for envelope in signed_heads[1:])
        with state.directory(RUNTIME) as fd:
            assert hashlib.sha256(state.read_file(fd, "EXO_OS_BUILD.json", mode=0o644)).hexdigest() == proof["installed_manifest_sha256"]
        proof.update({"state": "installed_flow_verified", "raw_image_sha256_host_attestation": proof["expected"]["image_sha256"],
                      "authority_before": public["head"], "published": published, "fresh": fresh,
                      "previous": previous, "current": current, "signed_heads": signed_heads,
                      "persistent_enrollment_left_for_overlay_discard": True, "independent_witness_deployment": False})
        return proof
    finally:
        server.close()
        thread.join(timeout=12)
        crypto.sign_doc = sign_doc
        assert not thread.is_alive(), "bounded synthetic witness server did not stop"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--preflight", action="store_true")
    choice.add_argument("--fixture", type=Path)
    arguments = parser.parse_args()
    result = preflight() if arguments.preflight else installed_flow(arguments.fixture)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

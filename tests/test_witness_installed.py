"""Host guard checks and one opt-in installed disposable Linux witness flow.

The test runner owns pytest/cryptography. Production Python -I imports neither.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

PROBE_PATH = Path(__file__).resolve().parents[1] / "tools/probe-witness-installed.py"
spec = importlib.util.spec_from_file_location("witness_installed_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def synthetic_certificates(directory):
    """Generate only throwaway mTLS material in the runner's private fixture."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "disposable-witness-CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
          .not_valid_after(now + datetime.timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    files = {"ca.pem": ca.public_bytes(serialization.Encoding.PEM)}
    for name, usage in (("server", ExtendedKeyUsageOID.SERVER_AUTH), ("client", ExtendedKeyUsageOID.CLIENT_AUTH)):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("witness.test")]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        files[name + ".pem"] = cert.public_bytes(serialization.Encoding.PEM)
        files[name + ".key"] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption())
    for name, raw in files.items():
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(raw)


@pytest.fixture
def expectations():
    return {"EXO_WITNESS_LINUX_ACCEPTANCE": "1", "EXO_WITNESS_EXPECTED_IMAGE_SHA256": "a" * 64,
            "EXO_WITNESS_EXPECTED_SOURCE_SHA256": "b" * 64, "EXO_WITNESS_EXPECTED_CORE_PIN": "c" * 40,
            "EXO_WITNESS_EXPECTED_OS_COMMIT": "d" * 40,
            "EXO_WITNESS_EXPECTED_VM_UUID": "bfe5b2e2-c9e8-4c7c-91cc-b205dd2fcd47"}


@pytest.mark.parametrize("field", ["EXO_WITNESS_LINUX_ACCEPTANCE", "EXO_WITNESS_EXPECTED_IMAGE_SHA256",
                                 "EXO_WITNESS_EXPECTED_SOURCE_SHA256", "EXO_WITNESS_EXPECTED_CORE_PIN",
                                 "EXO_WITNESS_EXPECTED_OS_COMMIT", "EXO_WITNESS_EXPECTED_VM_UUID"])
def test_installed_flow_requires_every_explicit_expectation(expectations, field):
    expectations.pop(field)
    with pytest.raises(ValueError):
        probe.expected_environment(expectations)


@pytest.mark.parametrize("change", ["schema", "build_profile", "source_sha256", "exo_pin", "exo_os_commit", "pin", "uuid"])
def test_installed_flow_refuses_provenance_or_vm_mismatch(expectations, change):
    expected = probe.expected_environment(expectations)
    manifest = {"schema": 2, "build_profile": "vmtest",
                **{field: expected[field] for field in ("source_sha256", "exo_pin", "exo_os_commit")}}
    pin, machine_uuid = expected["exo_pin"], expected["vm_uuid"]
    probe.match_provenance(expected, manifest, pin, machine_uuid)
    if change == "pin":
        pin = "e" * 40
    elif change == "uuid":
        machine_uuid = "a3d8ca93-bc18-4ebe-a84b-c77d1f21b623"
    else:
        manifest[change] = 1 if change == "schema" else "wrong"
    with pytest.raises(ValueError):
        probe.match_provenance(expected, manifest, pin, machine_uuid)


@pytest.mark.parametrize("existing", ["directory", "marker", "dangling_symlink"])
def test_installed_flow_never_reuses_or_removes_enrollment(tmp_path, existing):
    base, marker = tmp_path / "enrollment", tmp_path / "marker"
    probe.refuse_enrollment(base, marker)
    if existing == "directory":
        base.mkdir()
    elif existing == "marker":
        marker.write_text("preserve")
    else:
        base.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="preexisting"):
        probe.refuse_enrollment(base, marker)
    assert os.path.lexists(base if existing != "marker" else marker)


def test_installed_flow_rejects_editable_runtime_import(tmp_path):
    runtime = tmp_path / "opt/exo"
    runtime.mkdir(parents=True)
    installed = runtime / "installed.py"
    installed.touch()
    assert probe.installed_origin(installed, runtime) == str(installed)
    editable = tmp_path / "checkout.py"
    editable.touch()
    with pytest.raises(ValueError, match="installed"):
        probe.installed_origin(editable, runtime)
    installed.unlink()
    installed.symlink_to(editable)
    with pytest.raises(ValueError, match="installed"):
        probe.installed_origin(installed, runtime)


def test_synthetic_certificates_are_bounded_regular_private_files(tmp_path):
    import ssl
    synthetic_certificates(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {"ca.pem", "client.pem", "client.key", "server.pem", "server.key"}
    for path in tmp_path.iterdir():
        assert path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
        assert path.stat().st_mode & 0o777 == 0o600 and path.stat().st_size <= 65536
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(tmp_path / "ca.pem"))
    context.load_cert_chain(str(tmp_path / "client.pem"), str(tmp_path / "client.key"))


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0 or
                    os.environ.get("EXO_WITNESS_LINUX_ACCEPTANCE") != "1",
                    reason="requires explicit fresh disposable Linux root VM and installed runtime")
def test_installed_admin_exports_provisioned_authority_and_persists_fresh_witness():
    """No exporter mocks: signer and network workers execute installed packages."""
    probe.expected_environment(os.environ)  # refuse missing expectations before fixture creation
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **{
        name: value for name, value in os.environ.items()
        if name == "EXO_WITNESS_LINUX_ACCEPTANCE" or name.startswith("EXO_WITNESS_EXPECTED_")}}
    def run(*arguments, timeout):
        return probe.run_probe([probe.PYTHON, "-I", "-B", str(PROBE_PATH), *arguments],
                               environment, timeout=timeout)
    preflight = run("--preflight", timeout=60)
    with tempfile.TemporaryDirectory(prefix="exo-witness-installed-", dir="/run") as directory:
        fixture = Path(directory)
        assert fixture.stat().st_uid == 0 and fixture.stat().st_mode & 0o777 == 0o700
        synthetic_certificates(fixture)
        evidence = run("--fixture", str(fixture), timeout=700)
    assert evidence["expected"] == preflight["expected"]
    assert evidence["state"] == "installed_flow_verified"
    assert evidence["persistent_enrollment_left_for_overlay_discard"] and not evidence["independent_witness_deployment"]
    print(json.dumps(evidence, sort_keys=True))  # retain with pytest -s in the outer harness log


@pytest.fixture
def signed_proof(tmp_path):
    from exo import crypto
    from exo.ledger_authority import digest
    from exo.ledger_authority.journal import AuthorityJournal
    from exo.ledger_authority.witness import WitnessJournal
    writer, checkpoint, witness_key = crypto.keygen(), crypto.keygen(), crypto.keygen()
    identity = {"version": 1, "stream_id": "proof-test", "checkpoint_pub": crypto.pubkey_b64(checkpoint),
                "writers": {"agent": {"pub": crypto.pubkey_b64(writer), "author": "agent", "authors": ["agent"]}}}
    authority = AuthorityJournal.provision(tmp_path / "authority", identity, {"agent": writer}, checkpoint)
    genesis = authority.head()
    witness = WitnessJournal.provision(tmp_path / "witness", identity, witness_key, genesis)
    prior = witness.head()
    cp = authority.append("agent", "one", {"seq": genesis["seq"], "chain": genesis["chain"]},
                          {"author": "agent", "kind": "note", "body": {"text": "fixture"},
                           "session": None, "refs": []})["checkpoint"]
    published = witness.observe(cp)
    policy = {"identity": identity, "genesis": genesis, "witness_pub": crypto.pubkey_b64(witness_key)}
    def signed(receipt, nonce):
        return crypto.sign_doc({"version": 1, "type": "exo-ledger-witness-head", "stream_id": identity["stream_id"],
                                "identity_hash": digest(identity), "genesis_hash": digest(genesis),
                                "nonce": nonce, "receipt": receipt, "pub": crypto.pubkey_b64(witness_key)}, witness_key)
    heads = [signed(prior if n < 2 else published, format(n + 1, "064x")) for n in range(5)]
    return policy, heads, prior, published, signed


def test_installed_proof_accepts_five_signed_parent_client_and_status_heads(signed_proof):
    policy, heads, prior, published, _ = signed_proof
    observations = probe.verify_signed_heads(policy, heads, prior, published)
    assert [item["phase"] for item in observations] == ["parent_prepublication", "client_prepublication",
        "parent_postpublication", "fresh_status_1", "fresh_status_2"]
    assert [item["head"] for item in observations] == heads
    assert len({item["head"]["nonce"] for item in observations}) == 5


@pytest.mark.parametrize("damage", ["old_four_head_proof", "duplicate_nonce", "signature", "pre_receipt", "post_receipt"])
def test_installed_proof_refuses_invalid_challenge_or_phase_receipt(signed_proof, damage):
    policy, heads, prior, published, signed = signed_proof
    if damage == "old_four_head_proof":
        heads.pop(0)
    elif damage == "duplicate_nonce":
        heads[1] = heads[0]
    elif damage == "signature":
        heads[0]["sig"] = "forged"
    elif damage == "pre_receipt":
        heads[0] = signed(published, heads[0]["nonce"])
    else:
        heads[-1] = signed(prior, heads[-1]["nonce"])
    with pytest.raises(probe.ProbeFailure) as error:
        probe.verify_signed_heads(policy, heads, prior, published)
    assert error.value.diagnostic["step"] == "proof"


@pytest.mark.parametrize("failure", ["deadline", "stdout", "stderr", "exit"])
def test_outer_probe_cleans_ignored_term_descendant_with_bounded_redacted_output(tmp_path, failure):
    pid_file = tmp_path / "child.pid"
    child_script = ("import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)")
    # The fake probe and CLI descendant share the outer session/group, exactly as
    # the real tools probe now launches its CLI. Separate worker sessions require
    # the installed Linux parent-death test, rather than a host containment claim.
    script = f"""
import os,signal,subprocess,sys,time,pathlib
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', {child_script!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not pathlib.Path({str(pid_file)!r}).exists():
    time.sleep(.01)
mode = {failure!r}
if mode in ('stdout', 'stderr'):
    os.write(1 if mode == 'stdout' else 2, b'PRIVATE_CREDENTIAL_RESPONSE' * 4000)
elif mode == 'exit':
    sys.exit(2)
time.sleep(30)
"""
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError) as error:
            probe.run_probe([sys.executable, "-c", script], {"PATH": "/usr/bin:/bin"}, timeout=3)
        assert time.monotonic() - started < 4
        assert "PRIVATE_CREDENTIAL_RESPONSE" not in str(error.value)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=1)
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                break
            time.sleep(.01)
        else:
            pytest.fail("outer probe left a running descendant")
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_command_collector_kills_active_cli_when_probe_is_cancelled(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    original = probe.selectors.DefaultSelector
    class CancelledSelector:
        def __enter__(self):
            raise probe.ProbeCancelled()
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(probe.selectors, "DefaultSelector", CancelledSelector)
    with pytest.raises(probe.ProbeCancelled):
        probe.collect_output(process, timeout=3)
    assert process.returncode == -signal.SIGKILL
    assert process.stdout.closed and process.stderr.closed
    monkeypatch.setattr(probe.selectors, "DefaultSelector", original)


def test_outer_probe_redacts_invalid_json_response_body():
    with pytest.raises(RuntimeError, match="invalid installed probe JSON response") as error:
        probe.run_probe([sys.executable, "-c", "print('PRIVATE_CREDENTIAL_RESPONSE')"],
                        {"PATH": "/usr/bin:/bin"}, timeout=3)
    assert "PRIVATE_CREDENTIAL_RESPONSE" not in str(error.value)
    assert error.value.__suppress_context__


def test_main_preserves_known_admin_refusal_without_private_message(tmp_path, monkeypatch, capsys):
    child = tmp_path / "private-admin-path.py"
    child.write_text("import json,sys; print(json.dumps({'state':'conflict','message':'PRIVATE_CREDENTIAL_RESPONSE'})); "
                     "print('PRIVATE_STDERR',file=sys.stderr); sys.exit(1)")
    monkeypatch.setattr(probe, "PYTHON", sys.executable)
    monkeypatch.setattr(probe, "ADMIN", str(child))
    monkeypatch.setattr(probe, "preflight", lambda: probe.cli("publish"))
    monkeypatch.setattr(sys, "argv", ["probe", "--preflight"])
    assert probe.main() == 1
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert value == {"state": "conflict", "diagnostic": {"step": "publish", "error_class": "CommandExit",
                                                       "cli_exit": 1, "admin_state": "conflict"}}
    assert len(output.out.encode()) < 512
    assert "PRIVATE" not in output.out + output.err and str(child) not in output.out + output.err


def test_outer_probe_surfaces_only_validated_safe_diagnostic():
    diagnostic = {"step": "publish", "error_class": "CommandExit", "cli_exit": 1, "admin_state": "conflict"}
    value = {"state": "conflict", "diagnostic": diagnostic}
    script = f"import sys; print({json.dumps(value)!r}); print('PRIVATE_STDERR',file=sys.stderr); sys.exit(1)"
    with pytest.raises(probe.ProbeFailure) as error:
        probe.run_probe([sys.executable, "-c", script], {"PATH": "/usr/bin:/bin"}, timeout=3)
    assert error.value.diagnostic == diagnostic
    assert "conflict" in str(error.value) and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("damage", ["extra", "step", "class", "state", "exit", "arbitrary"])
def test_outer_probe_rejects_arbitrary_failure_response_without_leaking(damage):
    diagnostic = {"step": "publish", "error_class": "CommandExit", "cli_exit": 1, "admin_state": "conflict"}
    value = {"state": "conflict", "diagnostic": diagnostic}
    if damage == "extra":
        diagnostic["message"] = "PRIVATE_CREDENTIAL_RESPONSE"
    elif damage == "step":
        diagnostic["step"] = "PRIVATE_CREDENTIAL_RESPONSE"
    elif damage == "class":
        diagnostic["error_class"] = "PRIVATE_CREDENTIAL_RESPONSE"
    elif damage == "state":
        diagnostic["admin_state"] = "PRIVATE_CREDENTIAL_RESPONSE"
    elif damage == "exit":
        diagnostic["cli_exit"] = "PRIVATE_CREDENTIAL_RESPONSE"
    else:
        value = {"state": "PRIVATE_CREDENTIAL_RESPONSE", "message": "PRIVATE_CREDENTIAL_RESPONSE"}
    script = f"import sys; print({json.dumps(value)!r}); sys.exit(1)"
    with pytest.raises(probe.ProbeFailure) as error:
        probe.run_probe([sys.executable, "-c", script], {"PATH": "/usr/bin:/bin"}, timeout=3)
    assert error.value.diagnostic == {"step": "proof", "error_class": "CommandExit", "cli_exit": 1}
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("step", ["preflight", "public-authority", "proof", "server-cleanup"])
def test_probe_classifies_assertions_by_fixed_step_without_message(step):
    with pytest.raises(probe.ProbeFailure) as error:
        with probe.diagnostic_step(step):
            raise AssertionError("PRIVATE_CREDENTIAL_RESPONSE")
    assert error.value.diagnostic == {"step": step, "error_class": "AssertionError"}
    assert "PRIVATE" not in str(error.value)

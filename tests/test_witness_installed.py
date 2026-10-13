"""Host guard checks and one opt-in installed disposable Linux witness flow.

The test runner owns pytest/cryptography. Production Python -I imports neither.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
        process = subprocess.run([probe.PYTHON, "-I", "-B", str(PROBE_PATH), *arguments],
                                 cwd="/", env=environment, capture_output=True, text=True,
                                 timeout=timeout, check=True)
        return json.loads(process.stdout)
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

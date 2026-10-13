"""Real TLS upstream, synthetic TEST credentials; no cloud or existing key access."""
import base64
import datetime
import http.server
import ipaddress
import json
import socket
import ssl
import threading
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from exo_os.egress import broker as module
from exo_os.egress.broker import Broker, Journal
from exo_os.egress.policy import EgressError, Profile, load_policy, validate_request
from exo_os.sysd.protocol import decode, encode

SECRET = "TEST-secret-+/token"
TEST_ORIGIN = "https://fixture.example"


def request(request_id="op1", **kw):
    return {"version": 1, "nonce": "nonce1", "request_id": request_id, "profile": "fixture",
            "method": "POST", "expected_origin": TEST_ORIGIN, "path": "/api/echo", "body": '{"hello":"fixture"}', **kw}


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "TEST-cert.pem", tmp_path / "TEST-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    calls = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            calls.append((self.path, self.headers["Authorization"], self.rfile.read(int(self.headers["Content-Length"]))))
            if self.path == "/api/trickle":
                try:
                    for byte in b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass
                return
            if self.path == "/api/drop":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.path == "/api/redirect":
                self.send_response(302)
                self.send_header("Location", "/api/echo")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            if self.path == "/api/large":
                self.wfile.write(b"x" * (module.MAX_RESPONSE + 1))
            else:
                self.wfile.write((SECRET + " " + base64.b64encode(SECRET.encode()).decode() + " " +
                                  "".join(f"\\u{ord(c):04x}" for c in SECRET)).encode())
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_context = ssl.create_default_context(cafile=str(cert_file))
    origin = f"https://127.0.0.1:{server.server_port}"
    monkeypatch.setitem(globals(), "TEST_ORIGIN", origin)
    yield origin, calls, client_context
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def broker(tmp_path, upstream, monkeypatch):
    origin, calls, context = upstream
    credential = tmp_path / "TEST-credential"
    credential.write_text(SECRET)
    # Fixture paths live under a non-root-owned tmp directory on macOS.
    monkeypatch.setattr(module, "read_credential", lambda directory, name: (directory / name).read_text())
    profiles = {"fixture": Profile(origin, ("POST",), "/api", credential.name)}
    return Broker(profiles, Journal(tmp_path / "journal.sqlite"), tmp_path, context=context), calls


def test_real_tls_injection_redacted_and_no_payload_retained(broker, tmp_path):
    service, calls = broker
    result = service.handle(request())
    assert calls == [("/api/echo", "Bearer " + SECRET, b'{"hello":"fixture"}')]
    assert result["state"] == "completed" and result["status"] == 200
    assert SECRET not in json.dumps(result) and "body" not in result
    replay = service.handle(request(nonce="nonce2"))
    assert len(calls) == 1 and replay["nonce"] == "nonce2"
    assert replay["state"] == "completed" and "not retained" in replay["message"] and "body" not in replay
    stored = b"".join(p.read_bytes() for p in tmp_path.glob("journal.sqlite*"))
    assert SECRET.encode() not in stored and b'hello' not in stored


def test_denied_route_never_reaches_upstream(broker):
    service, calls = broker
    for index, kw in enumerate(({"profile": "absent"}, {"method": "GET"}, {"path": "/apiary/echo"})):
        assert service.handle(request(str(index), **kw))["state"] == "refused"
    assert calls == []


def test_redirect_never_followed(broker):
    service, calls = broker
    result = service.handle(request(path="/api/redirect"))
    assert result["status"] == 302 and "Redirect refused" in result["message"]
    assert [call[0] for call in calls] == ["/api/redirect"]


def test_lost_upstream_reply_unknown_and_never_repeated(broker):
    service, calls = broker
    result = service.handle(request(path="/api/drop"))
    assert result["state"] == "unknown" and result["retryable"] is False
    assert service.handle(request(path="/api/drop", nonce="another"))["state"] == "unknown"
    assert len(calls) == 1


def test_inflight_survives_restart_never_repeats(broker):
    service, calls = broker
    req = request()
    import hashlib
    fingerprint = hashlib.sha256(encode({k: v for k, v in req.items() if k not in {"nonce", "version"}})).hexdigest()
    service.journal.reserve("op1", fingerprint)
    service.journal = Journal(service.journal.path)
    assert service.handle(req)["state"] == "unknown" and calls == []


def test_same_id_changed_operation_refused(broker):
    service, calls = broker
    service.handle(request())
    with pytest.raises(EgressError, match="different operation"):
        service.handle(request(body="different"))
    assert len(calls) == 1


def test_response_bound_and_audit_failure(broker, monkeypatch):
    service, calls = broker
    result = service.handle(request(path="/api/large"))
    assert "body" not in result and "exceeded" in result["message"]
    def fail(*args):
        raise OSError("fixture audit disk failure")
    monkeypatch.setattr(service.journal, "finish", fail)
    assert service.handle(request("op2"))["state"] == "unknown"
    assert service.handle(request("op2"))["state"] == "unknown"
    assert len(calls) == 2


def test_default_empty_policy_and_tls_verification(tmp_path, upstream, monkeypatch):
    monkeypatch.setattr("exo_os.egress.policy.trusted_path", lambda path: path)
    policy = Path(__file__).parents[1] / "image/mkosi.extra/etc/exo-os/egress.toml"
    assert load_policy(policy) == {}
    origin, calls, _ = upstream
    service = Broker({"fixture": Profile(origin, ("POST",), "/api", "test")}, Journal(tmp_path / "db"), tmp_path)
    monkeypatch.setattr(module, "read_credential", lambda *args: SECRET)
    assert service.handle(request())["state"] == "refused"  # self-signed TEST certificate untrusted
    assert calls == []
    unsafe = ssl._create_unverified_context()
    with pytest.raises(EgressError, match="verification"):
        Broker({}, Journal(tmp_path / "db2"), tmp_path, context=unsafe)


@pytest.mark.parametrize("path", ["//evil/", "https://evil/api", "/api/../other", "/api/./x", "/api/%2e%2e/x",
                                   "/api%2fother", "/api\\x", "/api?x=1", "/api#frag", "/api//x", "/api;other"])
def test_path_escape_refused(path):
    with pytest.raises(EgressError):
        validate_request(request(path=path))


@pytest.mark.parametrize("extra", [{"host": "evil"}, {"headers": {}}, {"proxy": "evil"}, {"Authorization": SECRET}])
def test_request_has_no_authority_or_header_fields(extra):
    with pytest.raises(EgressError):
        validate_request({**request(), **extra})


def test_strict_wire():
    with pytest.raises(ValueError):
        decode(b'{"profile":"one","profile":"two"}')
    with pytest.raises(ValueError):
        decode(b'{"body":NaN}')


def test_parallel_duplicate_only_sends_once(broker):
    service, calls = broker
    results = []
    threads = [threading.Thread(target=lambda: results.append(service.handle(request()))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1 and len(results) == 4
    assert all(r["state"] in {"unknown", "completed"} for r in results)


def test_credentials_category_floor():
    from exo import gate

    from exo_os.plugin import TOOLS
    classification = gate.classify({"tool": "credential_request", "args": request()})
    assert classification.category == "credentials"
    assert classification.category in gate.NO_GRANT
    assert gate.decide(classification).outcome == "ask"
    tool = next(t for t in TOOLS if t[0] == "credential_request")
    assert tool[2] == "high"


def test_drip_headers_have_wall_clock_deadline(broker, monkeypatch):
    service, calls = broker
    monkeypatch.setattr(module, "TIMEOUT", 0.2)
    start = time.monotonic()
    result = service.handle(request(path="/api/trickle"))
    assert time.monotonic() - start < 0.8
    assert result["state"] == "unknown" and result["retryable"] is False
    assert service.handle(request(path="/api/trickle"))["state"] == "unknown"
    assert len(calls) == 1


def test_bound_origin_mismatch_refuses_without_send(broker):
    service, calls = broker
    assert service.handle(request(expected_origin="https://other.example"))["state"] == "refused"
    assert calls == []

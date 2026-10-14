"""Durably reserve each operation before HTTPS; never resend an uncertain operation."""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import socket
import sqlite3
import ssl
import stat
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..sysd.protocol import encode
from .policy import MAX_RESPONSE, TIMEOUT, EgressError, validate_request


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, outcome TEXT)")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=TIMEOUT)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, request_id: str, fingerprint: str):
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT fingerprint,outcome FROM requests WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise EgressError("request_id already belongs to a different operation")
                return json.loads(prior[1]) if prior[1] else {
                    "state": "unknown", "retryable": False, "message": "Reserved operation may have completed; never resend"}
            if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 100000:
                raise EgressError("idempotency journal is full; administrator intervention required")
            db.execute("INSERT INTO requests VALUES (?,?,NULL)", (request_id, fingerprint))
        return None

    def finish(self, request_id: str, outcome: dict):
        # Persist no request/response body, path, credential or headers.
        saved = {k: v for k, v in outcome.items() if k != "body"}
        if outcome["state"] == "completed":
            saved["message"] = "Already completed; original response payload was not retained"
        with self.lock, self.connect() as db:
            db.execute("UPDATE requests SET outcome=? WHERE id=?", (json.dumps(saved), request_id))


def read_credential(directory: Path, name: str) -> str:
    path = directory / name
    # systemd owns the credentials mount; its leaves may belong to the service UID.
    for part in (path, directory, *directory.parents):
        info = part.lstat()
        allowed = {0, os.geteuid()} if part in (path, directory) else {0}
        if stat.S_ISLNK(info.st_mode) or info.st_uid not in allowed or info.st_mode & 0o022:
            raise EgressError("credential path is not system controlled")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 4096:
        raise EgressError("invalid credential file")
    secret = path.read_text().rstrip("\n")
    if not secret or any(ord(c) < 33 or ord(c) > 126 for c in secret):
        raise EgressError("invalid bearer credential")
    return secret


class Broker:
    def __init__(self, profiles, journal: Journal, credentials: Path, *, context=None):
        self.profiles, self.journal, self.credentials = profiles, journal, credentials
        self.context = context or ssl.create_default_context()
        # Resolve only on trusted service startup: libc DNS cannot obey a socket
        # timeout. Requests use one fixed address, retaining HTTPS hostname checks.
        self.addresses = {}
        for profile in profiles.values():
            parsed = urlsplit(profile.origin)
            answers = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
            if not answers:
                raise EgressError(f"no addresses for {parsed.hostname}")
            self.addresses[profile.origin] = answers
        if not self.context.check_hostname or self.context.verify_mode != ssl.CERT_REQUIRED:
            raise EgressError("TLS verification must be enabled")

    def handle(self, raw):
        request = validate_request(raw)
        identity = {k: v for k, v in request.items() if k not in {"nonce", "version"}}
        fingerprint = hashlib.sha256(encode(identity)).hexdigest()
        result = {"version": 1, "nonce": request["nonce"], "request_id": request["request_id"], "retryable": False}
        # Reserve before route resolution, so a refused ID cannot later acquire a new meaning.
        prior = self.journal.reserve(request["request_id"], fingerprint)
        if prior is not None:
            return {**result, **prior}
        profile = self.profiles.get(request["profile"])
        if (profile is None or request["expected_origin"] != profile.origin or
                not profile.permits(request["method"], request["path"])):
            outcome = {"state": "refused", "message": "Route denied by administrator policy", "retryable": False}
        else:
            outcome = self.send(profile, request)
        try:
            self.journal.finish(request["request_id"], outcome)
        except (sqlite3.Error, OSError):
            # HTTPS may have mutated the upstream already. Reservation remains unknown.
            return {**result, "state": "unknown", "message": "Outcome could not be durably recorded; never resend"}
        return {**result, **outcome}

    def send(self, profile, request):
        conn = None
        watchdog = None
        sent = False
        try:
            secret = read_credential(self.credentials, profile.credential)
            parsed = urlsplit(profile.origin)
            conn = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=TIMEOUT, context=self.context)
            def connect_fixed(address, timeout, source_address=None):
                last = None
                for family, kind, proto, _, sockaddr in self.addresses[profile.origin]:
                    sock = socket.socket(family, kind, proto)
                    try:
                        sock.settimeout(max(0.001, deadline - time.monotonic()))
                        sock.connect(sockaddr)
                        if time.monotonic() >= deadline:
                            raise TimeoutError()
                        sock.settimeout(deadline - time.monotonic())
                        return sock
                    except BaseException as exc:
                        last = exc
                        sock.close()
                raise last
            conn._create_connection = connect_fixed
            deadline = time.monotonic() + TIMEOUT
            conn.connect()
            transport = conn.sock
            def expire():
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            watchdog = threading.Timer(max(0.001, deadline - time.monotonic()), expire)
            watchdog.daemon = True
            watchdog.start()
            transport.settimeout(max(0.001, deadline - time.monotonic()))
            sent = True  # errors in request() may occur after some/all bytes left the machine.
            conn.request(request["method"], request["path"], body=request["body"].encode(),
                         headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json",
                                  "Accept": "application/json", "Connection": "close"})
            response = conn.getresponse()
            # http.client never follows redirects. Never expose Location or response headers.
            if 300 <= response.status < 400:
                return {"state": "completed", "status": response.status, "retryable": False,
                        "message": "Redirect refused; request was sent only to configured origin"}
            body = bytearray()
            while len(body) <= MAX_RESPONSE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                transport.settimeout(remaining)
                chunk = response.read1(min(4096, MAX_RESPONSE + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if len(body) > MAX_RESPONSE:
                return {"state": "completed", "status": response.status, "retryable": False,
                        "message": "Response exceeded limit; payload withheld"}
            return {"state": "completed", "status": response.status, "retryable": False,
                    "message": "HTTP response received; upstream payload and headers withheld"}
        except Exception:  # noqa: BLE001 -- fail closed without credential-bearing exception details
            # Exceptions can contain request data. Do not expose or log them.
            return {"state": "unknown" if sent else "refused", "retryable": False,
                    "message": "Transport outcome uncertain; never resend" if sent else "Credential or TLS connection unavailable"}
        finally:
            if watchdog:
                watchdog.cancel()
            if conn:
                conn.close()

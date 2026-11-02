"""The web container's healthcheck follows the transport web serves (ADR 0091 point 3)."""

import datetime as dt
import http.server
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.healthcheck import healthz_url, main


def test_plain_http_without_a_certificate() -> None:
    assert healthz_url({}) == "http://127.0.0.1:8000/healthz"
    assert healthz_url({"WEB_TLS_CERT_FILE": ""}) == "http://127.0.0.1:8000/healthz"


def test_https_when_web_serves_tls() -> None:
    assert healthz_url({"WEB_TLS_CERT_FILE": "/web-tls/cert.pem"}) == (
        "https://127.0.0.1:8000/healthz"
    )


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Stay silent."""


def _self_signed(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "web.internal")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture
def tls_server(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    cert_file, key_file = _self_signed(tmp_path)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Ok)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], cert_file
    server.shutdown()
    server.server_close()


def test_a_healthy_tls_web_passes_although_the_certificate_names_another_host(
    tls_server: tuple[int, Path],
) -> None:
    port, cert_file = tls_server

    assert main({"WEB_TLS_CERT_FILE": str(cert_file)}, port=port) == 0


def test_plain_http_against_a_tls_web_fails(tls_server: tuple[int, Path]) -> None:
    port, _ = tls_server

    assert main({}, port=port) == 1


def test_nothing_listening_fails() -> None:
    assert main({}, port=1) == 1

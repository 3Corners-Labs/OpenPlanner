"""Outbound TLS verifies certificates (ASVS V12.3.2, V12.3.5).

The SMTP adapter hands a verifying context to ``starttls`` and ``SMTP_SSL``, the log shipper's
context verifies the chain and the host name, and no code in ``app/`` switches verification off
except the certificate probe's second, read-only handshake (ADR 0064) and the web healthcheck's
loopback request (ADR 0091). The mail server is the fake of ``tests/fakes/smtp.py``.
"""

import ast
import ssl
from pathlib import Path

import pytest

from app.adapters.log_senders import tls_context
from app.adapters.smtp import SmtpConfig, SmtpMailSender
from app.domain.channels import ChannelKind
from app.domain.ports import OutboundMessage
from tests.fakes.smtp import FakeSmtpServer

APP = Path(__file__).resolve().parents[2] / "app"
UNVERIFIED_ON_PURPOSE = {"adapters/tls_probe.py", "healthcheck.py"}
"""The probe reads the certificate of a server the host does not trust, after a verified try; the
healthcheck asks the container's own web on the loopback whether it is alive, sending nothing."""

MESSAGE = OutboundMessage(
    channel=ChannelKind.EMAIL, recipient="ada@example.org", subject="Onderwerp", text="Tekst"
)


def _verifies(context: ssl.SSLContext) -> bool:
    return context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.parametrize("implicit", [False, True], ids=["starttls", "implicit-tls"])
async def test_the_smtp_connection_verifies_the_servers_certificate(
    smtp_server: FakeSmtpServer, implicit: bool
) -> None:
    config = SmtpConfig("smtp", 587, "noreply@example.nl", starttls=True, implicit_tls=implicit)

    await SmtpMailSender(config).send(MESSAGE)

    [connection] = smtp_server.connections
    assert len(connection.contexts) == 1
    assert _verifies(connection.contexts[0])


def test_the_log_shippers_context_verifies_chain_and_host_name() -> None:
    assert _verifies(tls_context(""))


def _unverifying_spellings(tree: ast.AST) -> list[int]:
    """Return the lines that turn certificate verification off."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "verify":
            if isinstance(node.value, ast.Constant) and node.value.value is False:
                lines.append(node.value.lineno)
        if isinstance(node, ast.Attribute) and node.attr in {"CERT_NONE", "CERT_OPTIONAL"}:
            lines.append(node.lineno)
        if isinstance(node, ast.Attribute) and node.attr == "_create_unverified_context":
            lines.append(node.lineno)
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
            and any(
                isinstance(t, ast.Attribute) and t.attr == "check_hostname" for t in node.targets
            )
        ):
            lines.append(node.lineno)
    return lines


def test_no_code_in_the_app_switches_certificate_verification_off() -> None:
    offenders = {
        f"{path.relative_to(APP).as_posix()}:{line}"
        for path in APP.rglob("*.py")
        if path.relative_to(APP).as_posix() not in UNVERIFIED_ON_PURPOSE
        for line in _unverifying_spellings(ast.parse(path.read_text(encoding="utf-8")))
    }

    assert not offenders


def test_the_scan_sees_each_way_of_switching_verification_off() -> None:
    source = (
        "import ssl, httpx\n"
        "httpx.AsyncClient(verify=False)\n"
        "c = ssl.SSLContext()\n"
        "c.check_hostname = False\n"
        "c.verify_mode = ssl.CERT_NONE\n"
        "ssl._create_unverified_context()\n"
    )

    assert sorted(_unverifying_spellings(ast.parse(source))) == [2, 4, 5, 6]

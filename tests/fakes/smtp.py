"""A mail server for ``smtplib.SMTP`` and ``smtplib.SMTP_SSL`` to connect to (ADR 0009, 0022).

The SMTP adapter opens one connection per message. The fake keeps every connection with the
server it reached, the steps taken on it and the mail sent over it, and ``fail`` makes a step
raise what a refusing server or a dead network would. The ``smtp_server`` fixture of
``tests/conftest.py`` puts it in place of both classes for one test.
"""

import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Literal

Step = Literal["connect", "starttls", "login", "send"]
"""A moment of the session at which ``FakeSmtpServer.fail`` can make the server refuse."""


def _refuse(failures: Mapping[Step, Exception], step: Step) -> None:
    """Raise the error the test set for ``step``, if it set one."""
    if step in failures:
        raise failures[step]


class SmtpConnection:
    """One connection to the fake server, as ``smtplib.SMTP(...)`` returns it.

    ``calls`` reads like the session: ``tls`` for a connection encrypted from the start, then
    ``starttls``, ``login:<user>`` and ``quit``. ``contexts`` holds every TLS context the code
    handed over, ``sent`` every message.
    """

    def __init__(
        self, host: str, port: int, timeout: float, failures: Mapping[Step, Exception]
    ) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.contexts: list[ssl.SSLContext] = []
        self.sent: list[EmailMessage] = []
        self._failures = failures

    def __enter__(self) -> "SmtpConnection":
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: ssl.SSLContext) -> None:
        _refuse(self._failures, "starttls")
        self.contexts.append(context)
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        _refuse(self._failures, "login")
        self.calls.append(f"login:{user}")

    def send_message(self, msg: EmailMessage) -> None:
        _refuse(self._failures, "send")
        self.sent.append(msg)


class FakeSmtpServer:
    """Stands in for ``smtplib.SMTP`` and ``smtplib.SMTP_SSL``: every connection the code opens.

    ``connect`` takes the place of ``SMTP`` and ``connect_tls`` that of ``SMTP_SSL``; both
    record the connection in ``connections``, oldest first.
    """

    def __init__(self) -> None:
        self.connections: list[SmtpConnection] = []
        self.failures: dict[Step, Exception] = {}

    def fail(self, step: Step, error: Exception) -> None:
        """Make ``step`` raise ``error`` on every later connection, as a real server might."""
        self.failures[step] = error

    def connect(self, host: str, port: int, timeout: float) -> SmtpConnection:
        """Open a plain connection, as ``smtplib.SMTP(host, port, timeout=...)`` does."""
        _refuse(self.failures, "connect")
        connection = SmtpConnection(host, port, timeout, self.failures)
        self.connections.append(connection)
        return connection

    def connect_tls(
        self, host: str, port: int, timeout: float, context: ssl.SSLContext
    ) -> SmtpConnection:
        """Open a connection encrypted from the start, as ``smtplib.SMTP_SSL`` does."""
        connection = self.connect(host, port, timeout)
        connection.contexts.append(context)
        connection.calls.append("tls")
        return connection

    @property
    def sent(self) -> list[EmailMessage]:
        """Every message sent, over all connections."""
        return [message for connection in self.connections for message in connection.sent]

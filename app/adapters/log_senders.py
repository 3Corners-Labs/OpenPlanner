"""The wire adapters of the log destinations (ADR 0085 point 2).

One class per ``DestinationKind``, all behind ``LogDestinationSender``. Each takes a batch of
masked JSON lines and delivers it; a failure is a ``DeliveryError`` with a machine reason and,
for HTTP, the status code, never a response body or the address. No line, address or
credential is logged from here: the shipper reports only counters and reasons.

    SplunkSender   POST <address>              Authorization: Splunk <token>   HEC events
    ElasticSender  POST <address>/_bulk        Authorization: ApiKey <key>     NDJSON, data stream
    HttpSender     POST <address>              Authorization: Bearer <token>   NDJSON
    SyslogSender   RFC 5424 over TLS or TCP (RFC 5425 / 6587 octet counting) or UDP

Addresses may be in private ranges: the platform usually is on the same network, and only the
superuser types the address (ADR 0085 point 6). The senders send log lines and nothing else,
follow no redirects and give up after the timeout.
"""

import asyncio
import json
import ssl
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.db.log_destinations import LogTarget
from app.domain.log_destinations import (
    DeliveryError,
    DestinationKind,
    LogDestination,
    Reason,
    parse_address,
)
from app.domain.ports import LogDestinationSender

DEFAULT_HEC_PATH = "/services/collector/event"
DEFAULT_DATA_STREAM = "logs-openplanner-default"
SYSLOG_FACILITY = 16
"""``local0``: a site routes it by facility and by the app name ``openplanner``."""
APP_NAME = "openplanner"

_SEVERITY = {"trace": 7, "debug": 7, "info": 6, "warning": 4, "error": 3, "critical": 2}
"""Syslog severities of the ``log.level`` values (RFC 5424, table 2)."""

_HOPELESS = frozenset({400, 413, 414, 422})
"""Statuses that say this batch is malformed for the platform; retrying cannot help."""

Timeout = Callable[[], float]


def tls_context(ca_pem: str) -> ssl.SSLContext:
    """Return a verifying context: the system roots, plus the destination's own authorities.

    Raises:
        DeliveryError: ``tls`` when the extra certificates cannot be read.
    """
    context = ssl.create_default_context()
    if ca_pem:
        try:
            context.load_verify_locations(cadata=ca_pem)
        except (ssl.SSLError, ValueError) as exc:
            raise DeliveryError(Reason.TLS) from exc
    return context


def _caused_by_tls(exc: BaseException | None) -> bool:
    """Return whether a TLS failure sits anywhere in the chain of causes."""
    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, ssl.SSLError | ssl.CertificateError):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def reason_for(exc: BaseException) -> Reason:
    """Map a transport exception to the closed list of reasons."""
    return Reason.TLS if _caused_by_tls(exc) else Reason.UNREACHABLE


def _status_error(status: int) -> DeliveryError:
    """Map a non-2xx HTTP status to a ``DeliveryError``."""
    if status in (401, 403):
        return DeliveryError(Reason.REFUSED, status)
    if status in _HOPELESS:
        return DeliveryError(Reason.REFUSED, status, drop=True)
    if status >= 500 or status in (408, 429):
        return DeliveryError(Reason.SERVER, status)
    return DeliveryError(Reason.REFUSED, status)


class HttpBase:
    """Common part of the three HTTP senders: the client, the headers and the status check.

    Args:
        destination: The configuration.
        secret: The opened credential, if any.
        timeout: Returns the current timeout in seconds.
        transport: An explicit transport (tests); the network by default.
    """

    def __init__(
        self,
        destination: LogDestination,
        secret: str | None,
        timeout: Timeout,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Store the configuration; the client is built on first use."""
        self._destination = destination
        self._secret = secret
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        """Return the client, building it with the destination's trust on first use."""
        if self._client is None:
            verify: ssl.SSLContext | bool = tls_context(self._destination.ca_pem)
            self._client = httpx.AsyncClient(
                transport=self._transport,
                verify=verify,
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        """Return the request headers; the credential is added by the subclass."""
        return {}

    def _url(self) -> str:
        """Return the URL to post to."""
        return self._destination.address

    def _body(self, lines: Sequence[str]) -> tuple[bytes, str]:
        """Return the request body and its content type."""
        raise NotImplementedError

    def _check(self, response: httpx.Response) -> None:
        """Raise unless the platform accepted the batch (subclasses look deeper)."""

    async def send(self, lines: Sequence[str]) -> None:
        """Post the batch.

        Raises:
            DeliveryError: With the reason and, when the platform answered, its status code.
        """
        body, content_type = self._body(lines)
        try:
            response = await self._http().post(
                self._url(),
                content=body,
                headers={"content-type": content_type, **self._headers()},
                timeout=self._timeout(),
            )
        except DeliveryError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise DeliveryError(reason_for(exc)) from None
        if not 200 <= response.status_code < 300:
            raise _status_error(response.status_code)
        self._check(response)

    async def aclose(self) -> None:
        """Close the client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _records(lines: Sequence[str]) -> list[dict[str, Any]]:
    """Parse the lines; a line that is not an object cannot occur (the tap filtered it)."""
    return [json.loads(line) for line in lines]


def _epoch(record: Mapping[str, Any]) -> float | None:
    """Return the record's ``@timestamp`` as epoch seconds, if it has a readable one."""
    stamp = record.get("@timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    except ValueError:
        return None


class SplunkSender(HttpBase):
    """Splunk HTTP Event Collector: one JSON event per line, sent as concatenated objects."""

    def _url(self) -> str:
        parts = parse_address(
            DestinationKind.SPLUNK_HEC, self._destination.address, production=False
        )
        if parts.path in ("", "/"):
            return self._destination.address.rstrip("/") + DEFAULT_HEC_PATH
        return self._destination.address

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Splunk {self._secret}"} if self._secret else {}

    def _body(self, lines: Sequence[str]) -> tuple[bytes, str]:
        events = []
        for record in _records(lines):
            event: dict[str, Any] = {"sourcetype": "_json", "event": record}
            stamp = _epoch(record)
            if stamp is not None:
                event["time"] = stamp
            if self._destination.index:
                event["index"] = self._destination.index
            if self._destination.source:
                event["source"] = self._destination.source
            events.append(json.dumps(event, separators=(",", ":")))
        return "".join(events).encode(), "application/json"


class ElasticSender(HttpBase):
    """Elasticsearch or OpenSearch: ``_bulk`` ``create`` actions into a data stream."""

    def _url(self) -> str:
        return self._destination.address.rstrip("/") + "/_bulk"

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"ApiKey {self._secret}"} if self._secret else {}

    def _body(self, lines: Sequence[str]) -> tuple[bytes, str]:
        action = json.dumps(
            {"create": {"_index": self._destination.index or DEFAULT_DATA_STREAM}},
            separators=(",", ":"),
        )
        return "".join(f"{action}\n{line}\n" for line in lines).encode(), "application/x-ndjson"

    def _check(self, response: httpx.Response) -> None:
        """A bulk answer can be 200 with item errors; those lines will never be accepted."""
        try:
            errors = response.json().get("errors")
        except ValueError:
            return
        if errors is True:
            raise DeliveryError(Reason.REFUSED, response.status_code, drop=True)


class HttpSender(HttpBase):
    """Any HTTP collector that takes newline-delimited JSON, with an optional bearer token."""

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._secret}"} if self._secret else {}

    def _body(self, lines: Sequence[str]) -> tuple[bytes, str]:
        return "".join(f"{line}\n" for line in lines).encode(), "application/x-ndjson"


def syslog_message(line: str, hostname: str) -> bytes:
    """Return the RFC 5424 message for one JSON line.

    ``<PRI>1 TIMESTAMP HOSTNAME openplanner - MSGID - MSG``: the structured JSON line is the
    message, ``MSGID`` is ``audit`` for an audit line and ``log`` for the others.
    """
    record = json.loads(line)
    severity = _SEVERITY.get(str(record.get("log.level")), 6)
    stamp = record.get("@timestamp")
    if not isinstance(stamp, str) or not stamp:
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    msgid = "audit" if record.get("log_type") == "audit" else "log"
    priority = SYSLOG_FACILITY * 8 + severity
    return f"<{priority}>1 {stamp} {hostname or '-'} {APP_NAME} - {msgid} - {line}".encode()


class SyslogSender:
    """RFC 5424 syslog over TLS or TCP (octet counting) or UDP.

    Args:
        destination: The configuration; the scheme of the address chooses the transport.
        timeout: Returns the current timeout in seconds.
    """

    def __init__(self, destination: LogDestination, timeout: Timeout) -> None:
        """Parse the address; the connection is opened on first use."""
        self._destination = destination
        self._timeout = timeout
        self._address = parse_address(DestinationKind.SYSLOG, destination.address, production=False)
        self._writer: asyncio.StreamWriter | None = None
        self._datagrams: asyncio.DatagramTransport | None = None

    async def send(self, lines: Sequence[str]) -> None:
        """Send one message per line.

        Raises:
            DeliveryError: ``unreachable``, ``tls``.
        """
        host = self._destination.source
        messages = [syslog_message(line, host) for line in lines]
        try:
            if self._address.scheme == "syslog+udp":
                await self._send_udp(messages)
            else:
                await self._send_stream(messages)
        except DeliveryError:
            await self.aclose()
            raise
        except (TimeoutError, OSError, ssl.SSLError, ssl.CertificateError) as exc:
            await self.aclose()
            raise DeliveryError(reason_for(exc)) from None

    async def _send_stream(self, messages: Sequence[bytes]) -> None:
        """Write the messages on the TCP or TLS connection, opening it first if needed."""
        if self._writer is None or self._writer.is_closing():
            context = (
                tls_context(self._destination.ca_pem)
                if self._address.scheme == "syslog+tls"
                else None
            )
            _, opened = await asyncio.wait_for(
                asyncio.open_connection(
                    self._address.host,
                    self._address.port,
                    ssl=context,
                    server_hostname=self._address.host if context else None,
                ),
                self._timeout(),
            )
            self._writer = opened
        writer = self._writer
        # RFC 6587 octet counting: "<length> <message>", so a newline in a message is harmless.
        writer.write(b"".join(b"%d %s" % (len(m), m) for m in messages))
        await asyncio.wait_for(writer.drain(), self._timeout())

    async def _send_udp(self, messages: Sequence[bytes]) -> None:
        """Send one datagram per message; UDP says nothing about delivery."""
        if self._datagrams is None:
            loop = asyncio.get_running_loop()
            transport, _ = await asyncio.wait_for(
                loop.create_datagram_endpoint(
                    asyncio.DatagramProtocol,
                    remote_addr=(self._address.host, self._address.port or 514),
                ),
                self._timeout(),
            )
            self._datagrams = transport
        assert self._datagrams is not None  # noqa: S101 - narrowed above
        for message in messages:
            self._datagrams.sendto(message)

    async def aclose(self) -> None:
        """Close the connection."""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
            self._writer = None
        if self._datagrams is not None:
            self._datagrams.close()
            self._datagrams = None


def build_sender(
    target: LogTarget, timeout: Timeout, transport: httpx.AsyncBaseTransport | None = None
) -> LogDestinationSender:
    """Return the sender of ``target``'s kind.

    Args:
        target: The destination with its opened credential.
        timeout: Returns the current timeout in seconds.
        transport: An explicit HTTP transport (tests).
    """
    destination = target.destination
    match destination.kind:
        case DestinationKind.SYSLOG:
            return SyslogSender(destination, timeout)
        case DestinationKind.SPLUNK_HEC:
            return SplunkSender(destination, target.secret, timeout, transport)
        case DestinationKind.ELASTIC:
            return ElasticSender(destination, target.secret, timeout, transport)
        case DestinationKind.HTTP:
            return HttpSender(destination, target.secret, timeout, transport)

import asyncio
from dataclasses import dataclass

from app.adapters.smtp import SmtpConfig
from app.domain.channels import ChannelKind
from app.domain.ports import IntegrationError, OutboundMessage


class RecordingSender:
    """MessageSender fake that records messages, or fails a given number of times.

    It is also a ``SenderDirectory`` that serves itself for every organisation and channel,
    unless the channel is in ``unconfigured``.
    """

    def __init__(
        self, failures: int = 0, unconfigured: frozenset[ChannelKind] = frozenset()
    ) -> None:
        self.sent: list[OutboundMessage] = []
        self.failures = failures
        self.unconfigured = unconfigured
        self.asked: list[tuple[int, ChannelKind]] = []

    async def sender(self, organisation_id: int, channel: ChannelKind) -> "RecordingSender | None":
        self.asked.append((organisation_id, channel))
        return None if channel in self.unconfigured else self

    async def send(self, message: OutboundMessage) -> None:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("smtp down")
        self.sent.append(message)


class RefusingSender:
    """MessageSender and ``SenderDirectory`` fake whose every delivery fails with ``code``."""

    def __init__(self, code: str, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable

    async def sender(self, organisation_id: int, channel: ChannelKind) -> "RefusingSender":
        return self

    async def send(self, message: OutboundMessage) -> None:
        raise IntegrationError(self.code, retryable=self.retryable)


@dataclass(frozen=True)
class Delivery:
    """One message and the SMTP server the sender chose for it."""

    config: SmtpConfig
    message: OutboundMessage


class SmtpRecorder:
    """Stands in for ``SmtpMailSender``: records which server each message would go through.

    Patch it over ``app.adapters.senders.SmtpMailSender`` so the real ``ConfiguredSenders``
    chooses between an organisation's own server and the environment fallback.
    """

    def __init__(self) -> None:
        self.deliveries: list[Delivery] = []

    def __call__(self, config: SmtpConfig) -> "RecordedSmtp":
        return RecordedSmtp(config, self.deliveries)


class RecordedSmtp:
    """The ``MessageSender`` that ``SmtpRecorder`` hands out for one server configuration."""

    def __init__(self, config: SmtpConfig, deliveries: list[Delivery]) -> None:
        self.config = config
        self.deliveries = deliveries

    async def send(self, message: OutboundMessage) -> None:
        await asyncio.sleep(0)  # yield, as a real send does, so concurrent messages interleave
        self.deliveries.append(Delivery(self.config, message))

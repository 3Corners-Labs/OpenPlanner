from app.domain.channels import ChannelKind
from app.domain.ports import OutboundMessage


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

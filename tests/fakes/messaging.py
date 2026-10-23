from app.domain.ports import OutboundMessage


class RecordingSender:
    """MessageSender fake that records messages, or fails a given number of times."""

    def __init__(self, failures: int = 0) -> None:
        self.sent: list[OutboundMessage] = []
        self.failures = failures

    async def send(self, message: OutboundMessage) -> None:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("smtp down")
        self.sent.append(message)

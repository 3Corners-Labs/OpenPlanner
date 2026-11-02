"""Fake of the instance probe (ADR 0086): answers what the test put in and remembers the asks."""

from app.domain.public_address import InstanceAnswer, InstanceReply


class FakeInstanceProbe:
    """Returns ``reply`` (or the one of ``by_url``) and remembers every ``(url, nonce)`` asked."""

    def __init__(self, reply: InstanceReply | None = None) -> None:
        self.reply = reply or InstanceReply(error="unreachable")
        self.by_url: dict[str, InstanceReply] = {}
        self.asked: list[tuple[str, str]] = []

    async def probe(self, url: str, nonce: str) -> InstanceReply:
        self.asked.append((url, nonce))
        return self.by_url.get(url, self.reply)


def healthy(instance_id: str) -> InstanceReply:
    """Return the reply of an installation reached over https through a trusted proxy."""
    from app.domain.public_address import Forwarded

    return InstanceReply(InstanceAnswer(instance_id, "https", Forwarded.HONOURED))

"""Fake of the ``MailDomainResolver`` port (ADR 0073)."""


class FakeTxtResolver:
    """Answers TXT lookups from a table and remembers every name asked.

    A name missing from ``records`` has no records (``()``); a name mapped to ``None`` fails to
    resolve, as a timeout or an unreachable resolver would.
    """

    def __init__(self, records: dict[str, tuple[str, ...] | None] | None = None) -> None:
        self.records = records or {}
        self.asked: list[str] = []

    async def txt_records(self, name: str) -> tuple[str, ...] | None:
        self.asked.append(name)
        return self.records.get(name, ())

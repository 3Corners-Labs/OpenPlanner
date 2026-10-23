"""In-memory token store and a fixed token source for adapter tests."""

from datetime import datetime
from typing import Any


class MemoryTokenStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    async def get(self, name: str) -> dict[str, Any] | None:
        return self.values.get(name)

    async def put(self, name: str, value: dict[str, Any], now: datetime) -> None:
        self.values[name] = value

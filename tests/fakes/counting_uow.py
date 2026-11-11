"""A fake ``UnitOfWork`` that counts its write transactions and delegates the rest."""

from typing import Any


class CountingUow:
    """A unit of work that counts its write (``locked``) transactions and delegates the rest."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.writes = 0

    def read(self) -> Any:
        """Open a read transaction."""
        return self.inner.read()

    def locked(self) -> Any:
        """Open a write transaction and count it."""
        self.writes += 1
        return self.inner.locked()

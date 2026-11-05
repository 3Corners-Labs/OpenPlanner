"""Fake of the ``ConfigurationTransfer`` port (ADR 0089): no SQLite file, no sealing."""

from app.domain.configuration_package import PackageSummary


class FakeTransfer:
    """``ConfigurationTransfer`` fake: records what it was asked and answers from attributes.

    Every package it reads is ``summary``; ``busy`` is what ``in_use`` answers.
    """

    def __init__(self, summary: PackageSummary) -> None:
        self.summary = summary
        self.exported: list[str] = []
        self.imported: list[tuple[bytes, str]] = []
        self.busy = False

    async def export(self, passphrase: str) -> bytes:
        self.exported.append(passphrase)
        return b"SQLite format 3\x00package"

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        return self.summary

    async def import_(self, package: bytes, passphrase: str) -> PackageSummary:
        self.imported.append((package, passphrase))
        return self.summary

    async def in_use(self) -> bool:
        return self.busy

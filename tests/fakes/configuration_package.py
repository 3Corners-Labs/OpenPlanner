"""Fake of the ``ConfigurationTransfer`` port (ADR 0089): no SQLite file, no sealing."""

from app.domain.configuration_document import Document, Mode, Scope
from app.domain.configuration_package import PackageSummary
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import V1


class FakeTransfer:
    """``ConfigurationTransfer`` fake: records what it was asked and answers from attributes.

    Every package it reads is ``summary``, converted to a document that holds ``root``.
    """

    def __init__(self, summary: PackageSummary, root: Entity | None = None) -> None:
        self.summary = summary
        self.root = root or Entity("installation")
        self.converted: list[tuple[bytes, str]] = []

    async def inspect(self, package: bytes, passphrase: str) -> PackageSummary:
        return self.summary

    async def to_document(self, package: bytes, passphrase: str) -> tuple[Document, PackageSummary]:
        self.converted.append((package, passphrase))
        document = Document(V1.name, Mode.REPLACE, Scope(), self.root, "digest", V1)
        return document, self.summary

"""The document under review is kept for one admin and scope until it lapses (ADR 0099)."""

from datetime import UTC, datetime, timedelta

from app.admin.reviewed_documents import ReviewedDocuments
from app.domain.configuration_document import Scope

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def test_an_entry_is_found_by_its_token_for_its_owner_and_scope() -> None:
    store = ReviewedDocuments()
    entry = store.keep("alice", Scope(), b"doc", "hash", NOW, 30)

    assert store.find("alice", Scope(), entry.token, NOW) == entry
    assert store.find("alice", Scope(), "other", NOW) is None
    assert store.find("bob", Scope(), entry.token, NOW) is None
    assert store.find("alice", Scope("acme"), entry.token, NOW) is None


def test_the_next_upload_for_the_same_owner_and_scope_replaces_the_entry() -> None:
    store = ReviewedDocuments()
    first = store.keep("alice", Scope(), b"one", "h1", NOW, 30)
    second = store.keep("alice", Scope(), b"two", "h2", NOW, 30)

    assert store.find("alice", Scope(), first.token, NOW) is None
    assert store.find("alice", Scope(), second.token, NOW) == second


def test_an_entry_lapses_after_its_minutes_and_can_be_discarded() -> None:
    store = ReviewedDocuments()
    entry = store.keep("alice", Scope(), b"doc", "hash", NOW, 30)

    assert store.find("alice", Scope(), entry.token, NOW + timedelta(minutes=29)) == entry
    assert store.find("alice", Scope(), entry.token, NOW + timedelta(minutes=30)) is None
    again = store.keep("alice", Scope(), b"doc", "hash", NOW, 30)
    store.discard("alice", Scope())
    assert store.find("alice", Scope(), again.token, NOW) is None

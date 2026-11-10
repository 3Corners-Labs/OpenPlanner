"""In-memory ``WallboardLinkStore`` and ``WallboardLinkUnitOfWork`` for the display links' tests.

The store keeps links in a dict and applies the rules of the SQL adapter: a link is consumed by
one conditional update, listing is filtered by the scope, and *last used* and *last refused* are
written only when older than the given moment.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.domain.authorization import Scope
from app.domain.wallboard_link import WallboardLink


@dataclass
class LinkData:
    """What the fake store holds."""

    links: dict[int, WallboardLink] = field(default_factory=dict)
    organisations: dict[str, int] = field(default_factory=dict)
    groups: dict[tuple[int, str], int] = field(default_factory=dict)
    next_id: int = 1


class InMemoryLinkStore:
    """The ``WallboardLinkStore`` over one shared ``LinkData``."""

    def __init__(self, data: LinkData) -> None:
        self.data = data

    async def list_wallboard_links(
        self, scope: Scope, organisation_slug: str
    ) -> list[WallboardLink]:
        found = [
            link
            for link in self.data.links.values()
            if link.organisation_slug == organisation_slug
            and (scope.everything or link.organisation_slug in scope.organisations)
        ]
        return sorted(found, key=lambda link: -(link.id or 0))

    async def get_wallboard_link(self, link_id: int) -> WallboardLink | None:
        return self.data.links.get(link_id)

    async def get_wallboard_link_by_token(self, token_hash: str) -> WallboardLink | None:
        return next((v for v in self.data.links.values() if v.token_hash == token_hash), None)

    async def get_wallboard_link_by_session(self, session_hash: str) -> WallboardLink | None:
        return next((v for v in self.data.links.values() if v.session_hash == session_hash), None)

    async def add_wallboard_link(self, link: WallboardLink) -> int:
        new_id = self.data.next_id
        self.data.next_id += 1
        self.data.links[new_id] = replace(link, id=new_id)
        return new_id

    async def consume_wallboard_link(
        self, link_id: int, at: datetime, session_hash: str, pinned_network: str | None
    ) -> bool:
        link = self.data.links.get(link_id)
        if link is None or link.used_at or link.revoked_at or link.expires_at <= at:
            return False
        self.data.links[link_id] = replace(
            link,
            used_at=at,
            last_used_at=at,
            session_hash=session_hash,
            pinned_network=pinned_network,
        )
        return True

    async def revoke_wallboard_link(self, link_id: int, at: datetime) -> bool:
        link = self.data.links.get(link_id)
        if link is None or link.revoked_at is not None:
            return False
        self.data.links[link_id] = replace(link, revoked_at=at)
        return True

    async def touch_wallboard_link(self, link_id: int, at: datetime, older_than: datetime) -> None:
        link = self.data.links.get(link_id)
        if link is not None and (link.last_used_at is None or link.last_used_at < older_than):
            self.data.links[link_id] = replace(link, last_used_at=at)

    async def mark_wallboard_link_refused(
        self, link_id: int, at: datetime, older_than: datetime
    ) -> bool:
        link = self.data.links.get(link_id)
        if link is None or not (link.last_refused_at is None or link.last_refused_at < older_than):
            return False
        self.data.links[link_id] = replace(link, last_refused_at=at)
        return True

    async def group_id_in_organisation(self, organisation_id: int, slug: str) -> int | None:
        return self.data.groups.get((organisation_id, slug))


class InMemoryLinkUnitOfWork:
    """The ``WallboardLinkUnitOfWork`` over one shared ``LinkData``."""

    def __init__(self, data: LinkData | None = None) -> None:
        self.data = data or LinkData()

    @asynccontextmanager
    async def locked(self) -> AsyncIterator[InMemoryLinkStore]:
        yield InMemoryLinkStore(self.data)

    @asynccontextmanager
    async def read(self) -> AsyncIterator[InMemoryLinkStore]:
        yield InMemoryLinkStore(self.data)

"""SQL for the display links of the wallboard (ADR 0117 point 20, step 14).

A mixin of ``SqlTransaction``. A row holds ``sha256`` of the token and of the session secret,
never the secrets. Listing is filtered by ``Scope`` on the organisation and the group, as every
admin query is (ADR 0014). ``consume_link`` is one conditional ``UPDATE``: of two requests with
the same token, one finds the link unused and the other does not, whatever the interleaving.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Select, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import GroupRow, OrganisationRow, WallboardLinkRow
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Scope
from app.domain.wallboard_link import WallboardLink


def _link(row: WallboardLinkRow, organisation_slug: str, group_slug: str | None) -> WallboardLink:
    """Map a row with the slugs of its organisation and group."""
    return WallboardLink(
        id=row.id,
        organisation_id=row.organisation_id,
        organisation_slug=organisation_slug,
        group_id=row.group_id,
        group_slug=group_slug,
        name=row.name,
        token_hash=row.token_hash,
        session_hash=row.session_hash,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        used_at=row.used_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        networks=tuple(n for n in row.networks.split(",") if n),
        pin=bool(row.pin),
        pinned_network=row.pinned_network,
        last_refused_at=row.last_refused_at,
    )


class WallboardLinkQueries:
    """The display links' part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    def _link_query(self) -> Select[Any]:
        """Return the select of a link with its organisation and group slugs."""
        return (
            select(WallboardLinkRow, OrganisationRow.slug, GroupRow.slug)  # type: ignore[return-value]
            .join(OrganisationRow, OrganisationRow.id == WallboardLinkRow.organisation_id)
            .outerjoin(GroupRow, GroupRow.id == WallboardLinkRow.group_id)
        )

    async def list_wallboard_links(
        self, scope: Scope, organisation_slug: str
    ) -> list[WallboardLink]:
        """Return the links of one organisation inside ``scope``, newest first."""
        query = (
            self._link_query()
            .where(OrganisationRow.slug == organisation_slug)
            .where(scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
            .order_by(WallboardLinkRow.id.desc())
        )
        return [
            _link(r, org, group) for r, org, group in (await self._session.execute(query)).all()
        ]

    async def get_wallboard_link(self, link_id: int) -> WallboardLink | None:
        """Return one link, or ``None``."""
        found = (
            await self._session.execute(self._link_query().where(WallboardLinkRow.id == link_id))
        ).first()
        return None if found is None else _link(*found)

    async def get_wallboard_link_by_token(self, token_hash: str) -> WallboardLink | None:
        """Return the link whose token has this hash, or ``None``."""
        found = (
            await self._session.execute(
                self._link_query().where(WallboardLinkRow.token_hash == token_hash)
            )
        ).first()
        return None if found is None else _link(*found)

    async def get_wallboard_link_by_session(self, session_hash: str) -> WallboardLink | None:
        """Return the link whose screen session has this hash, or ``None``."""
        found = (
            await self._session.execute(
                self._link_query().where(WallboardLinkRow.session_hash == session_hash)
            )
        ).first()
        return None if found is None else _link(*found)

    async def add_wallboard_link(self, link: WallboardLink) -> int:
        """Store a link and return its id."""
        row = WallboardLinkRow(
            organisation_id=link.organisation_id,
            group_id=link.group_id,
            name=link.name[:80],
            token_hash=link.token_hash,
            created_by=link.created_by[:255],
            created_at=link.created_at,
            expires_at=link.expires_at,
            networks=",".join(link.networks),
            pin=link.pin,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def consume_wallboard_link(
        self, link_id: int, at: datetime, session_hash: str, pinned_network: str | None
    ) -> bool:
        """Use the link up: ``True`` only for the one request that finds it unused and active."""
        result = await self._session.execute(
            update(WallboardLinkRow)
            .where(
                WallboardLinkRow.id == link_id,
                WallboardLinkRow.used_at.is_(None),
                WallboardLinkRow.revoked_at.is_(None),
                WallboardLinkRow.expires_at > at,
            )
            .values(
                used_at=at,
                last_used_at=at,
                session_hash=session_hash,
                pinned_network=pinned_network,
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def revoke_wallboard_link(self, link_id: int, at: datetime) -> bool:
        """Revoke a link that is not revoked yet; return whether it was."""
        result = await self._session.execute(
            update(WallboardLinkRow)
            .where(WallboardLinkRow.id == link_id, WallboardLinkRow.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def touch_wallboard_link(self, link_id: int, at: datetime, older_than: datetime) -> None:
        """Write *last used* unless it is newer than ``older_than``."""
        await self._session.execute(
            update(WallboardLinkRow)
            .where(
                WallboardLinkRow.id == link_id,
                or_(
                    WallboardLinkRow.last_used_at.is_(None),
                    WallboardLinkRow.last_used_at < older_than,
                ),
            )
            .values(last_used_at=at)
        )

    async def mark_wallboard_link_refused(
        self, link_id: int, at: datetime, older_than: datetime
    ) -> bool:
        """Record a refusal unless one was recorded after ``older_than``; ``True`` when written."""
        result = await self._session.execute(
            update(WallboardLinkRow)
            .where(
                WallboardLinkRow.id == link_id,
                or_(
                    WallboardLinkRow.last_refused_at.is_(None),
                    WallboardLinkRow.last_refused_at < older_than,
                ),
            )
            .values(last_refused_at=at)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def organisation_id_of_slug(self, slug: str) -> int | None:
        """Return the id of an organisation, or ``None``."""
        return await self._session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == slug)
        )

    async def group_id_in_organisation(self, organisation_id: int, slug: str) -> int | None:
        """Return the id of a group of the organisation, or ``None``."""
        return await self._session.scalar(
            select(GroupRow.id).where(
                GroupRow.organisation_id == organisation_id, GroupRow.slug == slug
            )
        )

"""SQL for the digest subscriptions and their run records (ADR 0117 point 16).

A mixin of ``SqlTransaction``. A subscription holds staff row ids, never an address; the run
record is unique per subscription and due date, which is what stops a second worker sending the
same digest twice. Listing is filtered by ``Scope`` on the organisation and the group, as every
admin query is (ADR 0014).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import (
    GroupRow,
    OrganisationRow,
    ReportDigestRunRow,
    ReportSubscriptionRow,
)
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Scope
from app.domain.report_digest import DigestPeriod, RunStatus, Subscription


def _ids(text: str) -> tuple[int, ...]:
    """Return the staff row ids of a stored recipient list."""
    return tuple(int(part) for part in text.split(",") if part.strip().isdigit())


def _subscription(
    row: ReportSubscriptionRow, organisation_slug: str, group_slug: str | None
) -> Subscription:
    """Map a row with the slugs of its organisation and group."""
    return Subscription(
        id=row.id,
        organisation_id=row.organisation_id,
        organisation_slug=organisation_slug,
        group_id=row.group_id,
        group_slug=group_slug,
        period=DigestPeriod(row.period),
        weekday=row.weekday,
        month_day=row.month_day,
        recipients=_ids(row.recipients),
        created_by=row.created_by,
        created_at=row.created_at,
    )


class ReportDigestQueries:
    """The digest's part of ``SqlTransaction``; needs ``self._session``."""

    _session: AsyncSession

    def _subscription_query(self) -> Select[Any]:
        """Return the select of a subscription with its organisation and group slugs."""
        return (
            select(ReportSubscriptionRow, OrganisationRow.slug, GroupRow.slug)  # type: ignore[return-value]
            .join(OrganisationRow, OrganisationRow.id == ReportSubscriptionRow.organisation_id)
            .outerjoin(GroupRow, GroupRow.id == ReportSubscriptionRow.group_id)
        )

    async def list_subscriptions(
        self, scope: Scope, organisation_slug: str | None = None
    ) -> list[Subscription]:
        """Return the subscriptions inside ``scope``, of one organisation when named."""
        query = self._subscription_query().order_by(
            OrganisationRow.slug, GroupRow.slug, ReportSubscriptionRow.id
        )
        if organisation_slug is not None:
            query = query.where(OrganisationRow.slug == organisation_slug)
        query = query.where(scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
        rows = await self._session.execute(query)
        return [_subscription(r, org, group) for r, org, group in rows.all()]

    async def list_all_subscriptions(self) -> list[Subscription]:
        """Return every subscription, for the worker that sends them."""
        rows = await self._session.execute(
            self._subscription_query().order_by(ReportSubscriptionRow.id)
        )
        return [_subscription(r, org, group) for r, org, group in rows.all()]

    async def get_subscription(self, subscription_id: int) -> Subscription | None:
        """Return one subscription, or ``None``."""
        found = (
            await self._session.execute(
                self._subscription_query().where(ReportSubscriptionRow.id == subscription_id)
            )
        ).first()
        return None if found is None else _subscription(*found)

    async def organisation_id_of(self, slug: str) -> int | None:
        """Return the id of an organisation, or ``None``."""
        return await self._session.scalar(
            select(OrganisationRow.id).where(OrganisationRow.slug == slug)
        )

    async def group_id_of(self, organisation_id: int, slug: str) -> int | None:
        """Return the id of a group of the organisation, or ``None``."""
        return await self._session.scalar(
            select(GroupRow.id).where(
                GroupRow.organisation_id == organisation_id, GroupRow.slug == slug
            )
        )

    async def count_subscriptions(self, organisation_id: int) -> int:
        """Return how many subscriptions an organisation has."""
        return int(
            await self._session.scalar(
                select(func.count()).where(ReportSubscriptionRow.organisation_id == organisation_id)
            )
            or 0
        )

    async def add_subscription(self, subscription: Subscription) -> int:
        """Store a subscription and return its id."""
        row = ReportSubscriptionRow(
            organisation_id=subscription.organisation_id,
            group_id=subscription.group_id,
            period=subscription.period.value,
            weekday=subscription.weekday,
            month_day=subscription.month_day,
            recipients=",".join(str(i) for i in subscription.recipients),
            created_by=subscription.created_by[:255],
            created_at=subscription.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def delete_subscription(self, subscription_id: int) -> bool:
        """Delete a subscription with its run records; return whether it existed."""
        result = await self._session.execute(
            delete(ReportSubscriptionRow).where(ReportSubscriptionRow.id == subscription_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def claim_digest_run(
        self,
        subscription_id: int,
        due_date: date,
        status: RunStatus,
        sent: int,
        skipped: int,
        at: datetime,
    ) -> bool:
        """Record a run; return ``False`` when this digest was already run (nothing is sent)."""
        try:
            async with self._session.begin_nested():
                self._session.add(
                    ReportDigestRunRow(
                        subscription_id=subscription_id,
                        due_date=due_date,
                        status=status.value,
                        sent=sent,
                        skipped=skipped,
                        ran_at=at,
                    )
                )
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def has_digest_run(self, subscription_id: int, due_date: date) -> bool:
        """Return whether the digest of ``due_date`` was already run."""
        return (
            await self._session.scalar(
                select(ReportDigestRunRow.id).where(
                    ReportDigestRunRow.subscription_id == subscription_id,
                    ReportDigestRunRow.due_date == due_date,
                )
            )
            is not None
        )

    async def last_digest_run(self, subscription_id: int) -> tuple[date, str, int] | None:
        """Return the due date, status and skipped count of the latest run, or ``None``."""
        row = (
            await self._session.execute(
                select(
                    ReportDigestRunRow.due_date,
                    ReportDigestRunRow.status,
                    ReportDigestRunRow.skipped,
                )
                .where(ReportDigestRunRow.subscription_id == subscription_id)
                .order_by(ReportDigestRunRow.due_date.desc())
                .limit(1)
            )
        ).first()
        return None if row is None else (row[0], row[1], row[2])

    async def digest_zone(self, organisation_id: int, group_id: int | None) -> str | None:
        """Return the zone of a digest: the group's, else the organisation's first group's."""
        query = select(GroupRow.timezone).where(GroupRow.organisation_id == organisation_id)
        if group_id is not None:
            query = query.where(GroupRow.id == group_id)
        return await self._session.scalar(query.order_by(GroupRow.name).limit(1))

    async def digest_locale(self, organisation_id: int) -> str | None:
        """Return the organisation's default language, the language of its digest mail."""
        return await self._session.scalar(
            select(OrganisationRow.default_locale).where(OrganisationRow.id == organisation_id)
        )

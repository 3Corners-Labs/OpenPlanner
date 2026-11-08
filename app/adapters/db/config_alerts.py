"""Reading and writing alert targets for a configuration document (ADR 0099 step 2).

An alert target is an email address or a Webex room with the least urgent level it gets, on the
platform or on one organisation (ADR 0082 point 9). It is known by its kind and address within
its owner; its deliveries (state) go with it when it is deleted. Neither side bumps the settings
version or writes an audit event: the caller does both once per apply.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_common import Writer, find_organisation_id
from app.adapters.db.models import AlertDeliveryRow, AlertTargetRow, OrganisationRow
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1_names import (
    ORGANISATION_ALERT_TARGET_KIND,
    PLATFORM_ALERT_TARGET_KIND,
)


async def read_alert_targets(session: AsyncSession) -> dict[str | None, tuple[Entity, ...]]:
    """Return the current targets by owner: an organisation's slug, or ``None`` for the platform."""
    rows = await session.execute(
        select(AlertTargetRow, OrganisationRow.slug)
        .join(OrganisationRow, OrganisationRow.id == AlertTargetRow.organisation_id, isouter=True)
        .order_by(AlertTargetRow.kind, AlertTargetRow.address)
    )
    found: dict[str | None, list[Entity]] = {}
    for target, slug in rows:
        kind = PLATFORM_ALERT_TARGET_KIND if slug is None else ORGANISATION_ALERT_TARGET_KIND
        found.setdefault(slug, []).append(
            Entity(kind, (target.kind, target.address), fields={"min_level": target.min_level})
        )
    return {owner: tuple(entities) for owner, entities in found.items()}


async def _owner_id(session: AsyncSession, change: Change) -> int | None:
    """Return the id of the organisation a target belongs to; ``None`` for the platform's.

    Raises:
        DomainError: ``organisation_not_found``.
    """
    if change.kind == PLATFORM_ALERT_TARGET_KIND:
        return None
    return await find_organisation_id(session, change.address[0])


async def _find(session: AsyncSession, owner: int | None, change: Change) -> AlertTargetRow | None:
    """Return the stored target with the change's kind and address."""
    kind, address = change.key
    scope = (
        AlertTargetRow.organisation_id.is_(None)
        if owner is None
        else AlertTargetRow.organisation_id == owner
    )
    return await session.scalar(
        select(AlertTargetRow).where(
            scope, AlertTargetRow.kind == kind, AlertTargetRow.address == address
        )
    )


async def _write_target(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, change the level of, or delete (with its deliveries) one alert target."""
    owner = await _owner_id(session, change)
    row = await _find(session, owner, change)
    if change.action is ChangeAction.DELETE:
        if row is not None:
            await session.execute(
                delete(AlertDeliveryRow).where(AlertDeliveryRow.target_id == row.id)
            )
            await session.delete(row)
        return
    level = str(change.values["min_level"])
    if row is None:
        kind, address = change.key
        session.add(
            AlertTargetRow(organisation_id=owner, kind=kind, address=address, min_level=level)
        )
    elif "min_level" in change.fields or change.action is ChangeAction.CREATE:
        row.min_level = level
    await session.flush()


WRITERS: dict[str, Writer] = {
    PLATFORM_ALERT_TARGET_KIND: _write_target,
    ORGANISATION_ALERT_TARGET_KIND: _write_target,
}
"""The writer of every kind this module owns, by kind name."""

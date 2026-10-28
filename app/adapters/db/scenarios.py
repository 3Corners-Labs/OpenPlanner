"""SQL queries of closure scenarios (ADR 0037 §4).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. Whose a scenario is (organisation, group) is decided in
``app.domain.scenario_service``; listing always names the organisation, so a group id never
reaches another organisation's scenarios, and takes the caller's ``Scope`` (ADR 0014): a group
scenario is listed when its group is in scope, an organisation scenario when its organisation
or any of its groups is (a group admin uses the organisation's scenarios). Texts are stored as
JSON per locale and validated again by the domain when read.
"""

import json
from typing import Protocol

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db import images
from app.adapters.db.contact_json import contacts_block, contacts_json
from app.adapters.db.models import ClosureImageRow, ClosureScenarioRow, GroupRow, OrganisationRow
from app.adapters.db.scope import scope_filter
from app.domain.authorization import Scope
from app.domain.images import ImageFile, ImageText
from app.domain.scenarios import ClosureScenario


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


async def _scenario(session: AsyncSession, row: ClosureScenarioRow) -> ClosureScenario:
    """Map a scenario row with its contact block and image reference."""
    return ClosureScenario(
        id=row.id,
        organisation_id=row.organisation_id,
        group_id=row.group_id,
        name=row.name,
        notices=json.loads(row.notices),
        headings=json.loads(row.headings),
        contacts=contacts_block(row.contacts) if row.contacts else None,
        image=await images.page_image(session, row.image_id),
    )


class ScenarioQueries:
    """The closure scenario part of ``SqlTransaction``."""

    async def list_scenarios(
        self: _Transaction, scope: Scope, organisation_id: int, group_id: int | None
    ) -> list[ClosureScenario]:
        """Return the scenarios of a group, or with ``group_id`` ``None`` of the organisation."""
        if group_id is None:
            in_organisation = (
                select(GroupRow.id)
                .where(GroupRow.organisation_id == ClosureScenarioRow.organisation_id)
                .where(scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
            )
            owner = and_(
                ClosureScenarioRow.group_id.is_(None),
                or_(scope_filter(scope, OrganisationRow.slug, None), exists(in_organisation)),
            )
        else:
            in_group = (
                select(GroupRow.id)
                .where(GroupRow.id == ClosureScenarioRow.group_id)
                .where(scope_filter(scope, OrganisationRow.slug, GroupRow.slug))
            )
            owner = and_(ClosureScenarioRow.group_id == group_id, exists(in_group))
        rows = await self._session.scalars(
            select(ClosureScenarioRow)
            .join(OrganisationRow, OrganisationRow.id == ClosureScenarioRow.organisation_id)
            .where(ClosureScenarioRow.organisation_id == organisation_id, owner)
            .order_by(ClosureScenarioRow.name, ClosureScenarioRow.id)
        )
        return [await _scenario(self._session, row) for row in rows]

    async def get_scenario(self: _Transaction, scenario_id: int) -> ClosureScenario | None:
        """Return the scenario with ``scenario_id``."""
        row = await self._session.get(ClosureScenarioRow, scenario_id)
        return await _scenario(self._session, row) if row else None

    async def add_scenario(self: _Transaction, scenario: ClosureScenario) -> ClosureScenario:
        """Insert a scenario (without image) and return it with its id."""
        row = ClosureScenarioRow(
            organisation_id=scenario.organisation_id,
            group_id=scenario.group_id,
            name=scenario.name,
            notices=json.dumps(dict(scenario.notices), sort_keys=True),
            headings=json.dumps(dict(scenario.headings), sort_keys=True),
            contacts=contacts_json(scenario.contacts) if scenario.contacts else None,
        )
        self._session.add(row)
        await self._session.flush()
        return await _scenario(self._session, row)

    async def save_scenario(self: _Transaction, scenario: ClosureScenario) -> None:
        """Store the name, texts and contact block of an existing scenario."""
        await self._session.execute(
            update(ClosureScenarioRow)
            .where(ClosureScenarioRow.id == scenario.id)
            .values(
                name=scenario.name,
                notices=json.dumps(dict(scenario.notices), sort_keys=True),
                headings=json.dumps(dict(scenario.headings), sort_keys=True),
                contacts=contacts_json(scenario.contacts) if scenario.contacts else None,
            )
        )

    async def delete_scenario(self: _Transaction, scenario_id: int) -> None:
        """Delete the scenario and its image row."""
        image_id = await self._session.scalar(
            select(ClosureScenarioRow.image_id).where(ClosureScenarioRow.id == scenario_id)
        )
        await self._session.execute(
            delete(ClosureScenarioRow).where(ClosureScenarioRow.id == scenario_id)
        )
        if image_id is not None:
            await self._session.execute(
                delete(ClosureImageRow).where(ClosureImageRow.id == image_id)
            )

    async def get_scenario_image(self: _Transaction, scenario_id: int) -> ImageFile | None:
        """Return the stored image of the scenario, if it has one."""
        return await images.get_image(self._session, ClosureScenarioRow, scenario_id)

    async def save_scenario_image(
        self: _Transaction, scenario_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        """Store the scenario's image with ``text``; ``image`` ``None`` keeps the stored file."""
        await images.save_image(self._session, ClosureScenarioRow, scenario_id, text, image)

    async def remove_scenario_image(self: _Transaction, scenario_id: int) -> None:
        """Delete the scenario's image, if it has one."""
        await images.remove_image(self._session, ClosureScenarioRow, scenario_id)

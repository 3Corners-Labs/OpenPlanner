"""Prepared closure scenarios in a configuration document: read and write.

The adapter side of ``app.domain.configuration_v1_scenarios`` (ADR 0099 step 2). A scenario
belongs to an organisation (``group_id`` NULL) or to one group and is known by its name within
that owner; its image is a row of ``closure_images`` that only this scenario points at, which
the existing image queries replace and remove (``app.adapters.db.images``). Neither the
settings version nor an audit event is written here.
"""

import json
from collections.abc import Mapping

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db import images
from app.adapters.db.config_common import Writer, find_organisation_id, invalid
from app.adapters.db.contact_json import contacts_block, contacts_json
from app.adapters.db.models import (
    ClosureImageRow,
    ClosureScenarioRow,
    GroupRow,
    OrganisationRow,
)
from app.domain import configuration_v1_names as names
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_tree import Action, Entity
from app.domain.configuration_values import Binary, Value
from app.domain.contacts import block_from_json, block_to_json
from app.domain.errors import DomainError, ErrorCode
from app.domain.images import ImageFile, ImageText

type Scenarios = tuple[
    dict[str, tuple[Entity, ...]],
    dict[tuple[str, str], tuple[Entity, ...]],
]
"""Organisation scenarios by organisation slug, group scenarios by (organisation, group) slugs."""


async def read_scenarios(session: AsyncSession) -> Scenarios:
    """Return every scenario with its image: of the organisations, and of the groups."""
    rows = (
        await session.execute(
            select(ClosureScenarioRow, OrganisationRow.slug, GroupRow.slug)
            .join(OrganisationRow, OrganisationRow.id == ClosureScenarioRow.organisation_id)
            .outerjoin(GroupRow, GroupRow.id == ClosureScenarioRow.group_id)
            .order_by(OrganisationRow.slug, GroupRow.slug, ClosureScenarioRow.name)
        )
    ).all()
    image_rows = {image.id: image for image in await session.scalars(select(ClosureImageRow))}
    by_organisation: dict[str, list[Entity]] = {}
    by_group: dict[tuple[str, str], list[Entity]] = {}
    for row, organisation, group in rows:
        block = contacts_block(row.contacts) if row.contacts else None
        fields: dict[str, Value] = {
            "notices": json.loads(row.notices),
            "headings": json.loads(row.headings),
            "contacts": block_to_json(block) if block is not None and not block.is_empty else None,  # type: ignore[dict-item]
        }
        image = image_rows.get(row.image_id) if row.image_id is not None else None
        # A single object that is not there is absent, not an empty tuple (it could not be
        # written back as a document).
        children: dict[str, tuple[Entity, ...]] = {}
        if image is not None:
            image_fields: dict[str, Value] = {
                "data": Binary(image.media_type, image.data),
                "alts": json.loads(image.alts),
                "decorative": image.decorative,
            }
            children["image"] = (
                Entity(names.SCENARIO_IMAGE_KIND, (), Action.UPSERT, image_fields),
            )
        if group is None:
            kind, target = (
                names.ORGANISATION_SCENARIO_KIND,
                by_organisation.setdefault(organisation, []),
            )
        else:
            kind, target = names.GROUP_SCENARIO_KIND, by_group.setdefault((organisation, group), [])
        target.append(Entity(kind, (row.name,), Action.UPSERT, fields, children))
    return (
        {slug: tuple(entities) for slug, entities in by_organisation.items()},
        {pair: tuple(entities) for pair, entities in by_group.items()},
    )


async def _scenario_row(session: AsyncSession, change: Change) -> ClosureScenarioRow | None:
    """Return the scenario a scenario or image change is about; ``None`` when there is none."""
    organisation = await find_organisation_id(session, change.address[0])
    name = change.address[-1] if change.kind == names.SCENARIO_IMAGE_KIND else change.key[0]
    group = await _group_id(session, organisation, change)
    owner = (
        ClosureScenarioRow.group_id.is_(None)
        if group is None
        else ClosureScenarioRow.group_id == group
    )
    return await session.scalar(
        select(ClosureScenarioRow).where(
            ClosureScenarioRow.organisation_id == organisation,
            ClosureScenarioRow.name == name,
            owner,
        )
    )


async def _group_id(session: AsyncSession, organisation: int, change: Change) -> int | None:
    """Return the id of the group a change sits under; ``None`` for an organisation's own."""
    if change.group is None:
        return None
    found = await session.scalar(
        select(GroupRow.id).where(
            GroupRow.slug == change.group, GroupRow.organisation_id == organisation
        )
    )
    if found is None:
        raise invalid("group")
    return int(found)


def _contacts(value: Value) -> str | None:
    """Return the contact block of a field as the JSON stored, ``None`` for no block."""
    if value is None:
        return None
    block = block_from_json(value)
    return None if block.is_empty else contacts_json(block)


def _dump(value: Value) -> str:
    """Return a JSON object field as the stored text."""
    return json.dumps(value, sort_keys=True)


async def write_scenario(session: AsyncSession, box: SecretBox | None, change: Change) -> None:
    """Create, update or delete a scenario of an organisation or a group.

    Raises:
        DomainError: ``invalid_configuration`` for an unknown organisation, group or scenario.
    """
    row = await _scenario_row(session, change)
    if change.action is ChangeAction.DELETE:
        if row is not None:
            image_id = row.image_id
            await session.delete(row)
            await session.flush()
            if image_id is not None:
                await session.execute(delete(ClosureImageRow).where(ClosureImageRow.id == image_id))
        return
    values = change.values
    if change.action is ChangeAction.CREATE:
        if row is not None:
            raise DomainError(ErrorCode.CONFIGURATION_OBJECT_EXISTS)
        organisation = await find_organisation_id(session, change.address[0])
        session.add(
            ClosureScenarioRow(
                organisation_id=organisation,
                group_id=await _group_id(session, organisation, change),
                name=change.key[0],
                notices=_dump(values["notices"]),
                headings=_dump(values.get("headings", {})),
                contacts=_contacts(values.get("contacts")),
            )
        )
    else:
        if row is None:
            raise invalid("name")
        if "notices" in change.fields:
            row.notices = _dump(values["notices"])
        if "headings" in change.fields:
            row.headings = _dump(values.get("headings", {}))
        if "contacts" in change.fields:
            row.contacts = _contacts(values.get("contacts"))
    await session.flush()


async def write_scenario_image(
    session: AsyncSession, box: SecretBox | None, change: Change
) -> None:
    """Create, replace or remove the image of a scenario, with its text alternative.

    Raises:
        DomainError: ``invalid_configuration`` for an unknown scenario or an image without a file.
    """
    row = await _scenario_row(session, change)
    if row is None or row.id is None:
        raise invalid("name")
    if change.action is ChangeAction.DELETE:
        await images.remove_image(session, ClosureScenarioRow, row.id)
        return
    values = change.values
    alts = values.get("alts", {})
    texts = {k: v for k, v in alts.items() if isinstance(v, str)} if isinstance(alts, dict) else {}
    text = ImageText(texts, values.get("decorative") is True)
    data = values.get("data")
    file = None
    if change.action is ChangeAction.CREATE or "data" in change.fields:
        if not isinstance(data, Binary):
            raise invalid("image")
        file = ImageFile(data.media_type, data.data)
    await images.save_image(session, ClosureScenarioRow, row.id, text, file)


WRITERS: Mapping[str, Writer] = {
    names.ORGANISATION_SCENARIO_KIND: write_scenario,
    names.GROUP_SCENARIO_KIND: write_scenario,
    names.SCENARIO_IMAGE_KIND: write_scenario_image,
}
"""The writers of this module, by the v1 kind they apply."""

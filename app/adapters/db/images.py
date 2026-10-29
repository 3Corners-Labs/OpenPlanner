"""SQL queries of closure and scenario images (ADR 0037 §3-4).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. An image row belongs to exactly one closure or scenario (its ``image_id``):
replacing the file inserts a new row and deletes the old one, and starting a closure from a
scenario stores a copy, so no row is ever shared and removing one never touches another.
The bytes are read only to serve the image, never to show a closure or scenario.
"""

import json
from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import ClosureImageRow, ClosureRow, ClosureScenarioRow
from app.domain.images import ImageFile, ImageText, PageImage

type ImageOwner = type[ClosureRow] | type[ClosureScenarioRow]
"""The tables whose rows point at an image through ``image_id``."""


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession


def image_text(alts: str, decorative: bool) -> ImageText:
    """Map the stored text alternative of an image."""
    return ImageText(json.loads(alts), decorative)


async def page_image(session: AsyncSession, image_id: int | None) -> PageImage | None:
    """Return what pages need to show the image ``image_id`` (no bytes), if any."""
    if image_id is None:
        return None
    found = (
        await session.execute(
            select(
                ClosureImageRow.media_type,
                ClosureImageRow.digest,
                ClosureImageRow.alts,
                ClosureImageRow.decorative,
            ).where(ClosureImageRow.id == image_id)
        )
    ).first()
    if found is None:  # pragma: no cover - guarded by the foreign key
        return None
    media_type, digest, alts, decorative = found
    return PageImage(media_type, digest, image_text(alts, decorative))


async def get_image(session: AsyncSession, owner: ImageOwner, owner_id: int) -> ImageFile | None:
    """Return the stored image of the closure or scenario ``owner_id``, if it has one."""
    found = (
        await session.execute(
            select(ClosureImageRow.media_type, ClosureImageRow.data)
            .join(owner, owner.image_id == ClosureImageRow.id)
            .where(owner.id == owner_id)
        )
    ).first()
    return ImageFile(found[0], found[1]) if found else None


async def save_image(
    session: AsyncSession,
    owner: ImageOwner,
    owner_id: int,
    text: ImageText,
    image: ImageFile | None,
) -> None:
    """Store the owner's image with ``text``; ``image`` ``None`` keeps the stored file."""
    current = await session.scalar(select(owner.image_id).where(owner.id == owner_id))
    alts = json.dumps(dict(text.alts), sort_keys=True)
    if image is None:
        await session.execute(
            update(ClosureImageRow)
            .where(ClosureImageRow.id == current)
            .values(alts=alts, decorative=text.decorative)
        )
        return
    row = ClosureImageRow(
        media_type=image.media_type,
        digest=image.digest,
        data=image.data,
        alts=alts,
        decorative=text.decorative,
    )
    session.add(row)
    await session.flush()
    await session.execute(update(owner).where(owner.id == owner_id).values(image_id=row.id))
    if current is not None:
        await session.execute(delete(ClosureImageRow).where(ClosureImageRow.id == current))


async def remove_image(session: AsyncSession, owner: ImageOwner, owner_id: int) -> None:
    """Delete the owner's image, if it has one."""
    current = await session.scalar(select(owner.image_id).where(owner.id == owner_id))
    if current is None:
        return
    await session.execute(update(owner).where(owner.id == owner_id).values(image_id=None))
    await session.execute(delete(ClosureImageRow).where(ClosureImageRow.id == current))


class ImageQueries:
    """The closure image part of ``SqlTransaction``."""

    async def get_closure_image(self: _Transaction, closure_id: int) -> ImageFile | None:
        """Return the stored image of the closure, if it has one."""
        return await get_image(self._session, ClosureRow, closure_id)

    async def save_closure_image(
        self: _Transaction, closure_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        """Store the closure's image with ``text``; ``image`` ``None`` keeps the stored file."""
        await save_image(self._session, ClosureRow, closure_id, text, image)

    async def remove_closure_image(self: _Transaction, closure_id: int) -> None:
        """Delete the closure's image, if it has one."""
        await remove_image(self._session, ClosureRow, closure_id)

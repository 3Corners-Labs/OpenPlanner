"""SQL queries of closure images (ADR 0037 §3).

A mixin of ``SqlTransaction``, so these queries run in the caller's transaction like every
other one. An image row belongs to exactly one closure: replacing the file inserts a new row
and deletes the old one, so no row is ever shared and removing one never touches another.
The bytes are read only to serve the image, never to show a closure.
"""

import json
from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import ClosureImageRow, ClosureRow
from app.domain.images import ImageFile, ImageText, PageImage


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession

    async def _delete(self, image_id: int) -> None:
        """Delete the image row ``image_id``."""
        ...


def image_text(alts: str, decorative: bool) -> ImageText:
    """Map the stored text alternative of an image."""
    return ImageText(json.loads(alts), decorative)


class ImageQueries:
    """The closure image part of ``SqlTransaction``."""

    async def page_image(self: _Transaction, image_id: int | None) -> PageImage | None:
        """Return what pages need to show the image ``image_id`` (no bytes), if any."""
        if image_id is None:
            return None
        found = (
            await self._session.execute(
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

    async def get_closure_image(self: _Transaction, closure_id: int) -> ImageFile | None:
        """Return the stored image of the closure, if it has one."""
        found = (
            await self._session.execute(
                select(ClosureImageRow.media_type, ClosureImageRow.data)
                .join(ClosureRow, ClosureRow.image_id == ClosureImageRow.id)
                .where(ClosureRow.id == closure_id)
            )
        ).first()
        return ImageFile(found[0], found[1]) if found else None

    async def save_closure_image(
        self: _Transaction, closure_id: int, text: ImageText, image: ImageFile | None
    ) -> None:
        """Store the closure's image with ``text``; ``image`` ``None`` keeps the stored file."""
        current = await self._session.scalar(
            select(ClosureRow.image_id).where(ClosureRow.id == closure_id)
        )
        alts = json.dumps(dict(text.alts), sort_keys=True)
        if image is None:
            await self._session.execute(
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
        self._session.add(row)
        await self._session.flush()
        await self._session.execute(
            update(ClosureRow).where(ClosureRow.id == closure_id).values(image_id=row.id)
        )
        if current is not None:
            await self._session.execute(
                delete(ClosureImageRow).where(ClosureImageRow.id == current)
            )

    async def remove_closure_image(self: _Transaction, closure_id: int) -> None:
        """Delete the closure's image, if it has one."""
        current = await self._session.scalar(
            select(ClosureRow.image_id).where(ClosureRow.id == closure_id)
        )
        if current is None:
            return
        await self._session.execute(
            update(ClosureRow).where(ClosureRow.id == closure_id).values(image_id=None)
        )
        await self._delete(current)

    async def _delete(self: _Transaction, image_id: int) -> None:
        """Delete the image row ``image_id``."""
        await self._session.execute(delete(ClosureImageRow).where(ClosureImageRow.id == image_id))

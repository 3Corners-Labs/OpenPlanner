"""The citizen's side of the booking pages in HTTP tests: the emailed link and confirming it.

Since ADR 0056 the management token reaches the citizen by email only; the booking page
redirects to "check your email". Tests read the newest booking's salt from the database and
derive the token as the mail composer does, then confirm with the page's POST.
"""

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import BookingRow
from app.domain.tokens import derive_token
from app.keys import management_token_key

SAME_ORIGIN = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}


async def emailed_token(client: httpx.AsyncClient) -> str:
    """Return the management token of the newest booking, as its email carries it."""
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with AsyncSession(app.state.engine) as session:
        salt = (
            await session.execute(
                select(BookingRow.management_token_salt).order_by(BookingRow.id.desc()).limit(1)
            )
        ).scalar_one()
    return derive_token(management_token_key(app.state.settings), salt)


async def confirm_by_link(client: httpx.AsyncClient, token: str) -> httpx.Response:
    """Confirm the booking behind ``token`` with the confirm page's button."""
    return await client.post(f"/b/{token}/confirm", headers=SAME_ORIGIN)


async def confirm_newest(client: httpx.AsyncClient) -> str:
    """Confirm the newest booking by its emailed link and return its token."""
    token = await emailed_token(client)
    response = await confirm_by_link(client, token)
    assert response.headers["location"] == f"/b/{token}?status=confirmed"
    return token

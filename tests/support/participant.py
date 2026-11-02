"""The participant's side of the booking pages in HTTP tests: the emailed link and confirming it.

Since ADR 0056 the management token reaches the participant by email only; the booking page
redirects to "check your email". Tests read the newest booking's salt from the database and
derive the token as the mail composer does. Since ADR 0093 opening that link trades the token
for a cookie and redirects to ``/b/m/{public_ref}``; ``open_link`` does that and returns the
address of the page, free of any token.
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


async def open_link(client: httpx.AsyncClient, token: str) -> str:
    """Open the emailed link as a browser does and return the page's address (no token).

    The client keeps the cookie that the exchange sets (ADR 0093).
    """
    response = await client.get(f"/b/{token}")
    assert response.status_code == 303
    page = response.headers["location"]
    assert page.startswith("/b/m/")
    return page.split("?")[0]


async def confirm_by_link(client: httpx.AsyncClient, token: str) -> httpx.Response:
    """Open the link and confirm the booking behind ``token`` with the confirm page's button."""
    page = await open_link(client, token)
    return await client.post(f"{page}/confirm", headers=SAME_ORIGIN)


async def confirm_newest(client: httpx.AsyncClient) -> str:
    """Confirm the newest booking by its emailed link and return its token."""
    token = await emailed_token(client)
    response = await confirm_by_link(client, token)
    assert response.headers["location"].endswith("?status=confirmed")
    return token

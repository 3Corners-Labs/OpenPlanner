"""The participant's side of the booking pages in HTTP tests: booking and the emailed link.

``book`` posts the demo group's booking form for Ada; the demo group is open on weekdays, so
``FRIDAY_0900`` is free the day after "now" (Thursday 1 January 2026 08:00 UTC).

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
"""The headers of a form the browser posts from the page itself."""
FRIDAY_0900 = "2026-01-02T08:00:00Z"
"""Friday 2 January 09:00 Amsterdam, a free slot of the demo group, as the form sends it."""
FRIDAY_0930 = "2026-01-02T08:30:00Z"
"""The slot after ``FRIDAY_0900``."""
ADA = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.org", "phone": ""}
"""Ada's details as the booking form sends them, without a phone number."""


async def book(
    client: httpx.AsyncClient, start: str = FRIDAY_0900, **fields: str
) -> httpx.Response:
    """Post the demo group's booking form for Ada at ``start``, with ``fields`` added or changed."""
    return await client.post(
        "/g/demo/book", data={"start": start, **ADA, **fields}, headers=SAME_ORIGIN
    )


async def booked(client: httpx.AsyncClient, start: str = FRIDAY_0900, **fields: str) -> str:
    """Book, confirm by the emailed link and return the management token."""
    response = await book(client, start, **fields)
    assert response.status_code == 303
    return await confirm_newest(client)


async def booked_page(client: httpx.AsyncClient, start: str = FRIDAY_0900) -> str:
    """Book and confirm Ada at ``start``; return the management page ``/b/m/{ref}`` (ADR 0093)."""
    return await open_link(client, await booked(client, start))


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

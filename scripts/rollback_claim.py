"""The claim the upgrade matrix lets the previous image make (ADR 0121 A30, gate item G43).

The previous image's claim of work is a signed-in agent's POST to the queue, which the matrix
cannot make: it runs without an identity provider, and a login must never be forged. What the
POST writes is one conditional update of the booking (``claim_booking``: confirmed and not
yet claimed). This script runs that update with whichever ``app`` package is on the path, so
inside the previous image it is the image's own code on the expanded database:

    claim URL AGENT ID...    claim each booking in order; print the code's path and the results
    claimed URL ID...        print the holder (``claimed_by``) of each booking, or null

The script uses only what the previous release's code also has (``create_engine``,
``SqlUnitOfWork.locked``, ``claim_booking``, ``get_booking``), so one file serves the image and
the checkout. ``claim`` prints ``{"code": ..., "claimed": [true, false, ...]}``; ``claimed``
prints ``{"<id>": holder}``. Standard library and the ``app`` package only.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime


async def claim_all(url: str, agent: str, booking_ids: list[int]) -> list[bool]:
    """Claim each booking in its own locked transaction; return whether each claim was taken.

    Args:
        url: The database URL.
        agent: The pseudonym written as the holder (never a name or email address).
        booking_ids: The bookings to claim, in order; a repeated id is a second claim.

    Returns:
        One flag per id: true when the booking was confirmed and unclaimed and is now held.
    """
    from app.adapters.db.engine import create_engine  # noqa: PLC0415
    from app.adapters.db.uow import SqlUnitOfWork  # noqa: PLC0415
    from app.domain.booking import Claim  # noqa: PLC0415

    engine = create_engine(url)
    try:
        taken = []
        for booking_id in booking_ids:
            async with SqlUnitOfWork(engine).locked() as tx:
                taken.append(await tx.claim_booking(booking_id, Claim(datetime.now(UTC), agent)))
        return taken
    finally:
        await engine.dispose()


async def claimed_by(url: str, booking_ids: list[int]) -> dict[int, str | None]:
    """Return the holder of each booking's claim, ``None`` where nobody holds it."""
    from app.adapters.db.engine import create_engine  # noqa: PLC0415
    from app.adapters.db.uow import SqlUnitOfWork  # noqa: PLC0415

    engine = create_engine(url)
    try:
        found: dict[int, str | None] = {}
        async with SqlUnitOfWork(engine).read() as tx:
            for booking_id in booking_ids:
                booking = await tx.get_booking(booking_id)
                claim = booking.claim if booking is not None else None
                found[booking_id] = claim.claimed_by if claim is not None else None
        return found
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    """Run ``claim`` or ``claimed`` (see the module docstring); print one line of JSON."""
    if len(argv) < 2 or argv[0] not in {"claim", "claimed"}:  # noqa: PLR2004
        sys.stderr.write("usage: rollback_claim.py claim URL AGENT ID... | claimed URL ID...\n")
        return 2
    if argv[0] == "claim":
        import app  # noqa: PLC0415

        ids = [int(value) for value in argv[3:]]
        taken = asyncio.run(claim_all(argv[1], argv[2], ids))
        print(json.dumps({"code": app.__file__, "claimed": taken}))  # noqa: T201
    else:
        held = asyncio.run(claimed_by(argv[1], [int(value) for value in argv[2:]]))
        print(json.dumps({str(key): value for key, value in held.items()}))  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

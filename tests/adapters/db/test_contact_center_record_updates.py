"""Each write of one field of the sealed contact-center record is read where it is written.

ADR 0124 point 8: the pause, the registration outcome and a rotation are separate writers of one
record; none may overwrite another's change with a copy it read earlier.
"""

import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.contact_center_connections import SealedConnections
from app.adapters.db.credentials import CredentialStore
from app.domain.contact_center import ConnectionChange, ContactCenterKind, Registration
from tests.support.configuration_package import NOW, SOURCE_BOX

WXCC = ContactCenterKind.WEBEX_CC
CHANGE = ConnectionChange(WXCC, "https://api.wxcc-eu1.cisco.com", "client", "cs", "rt")


async def test_concurrent_writers_of_different_fields_all_keep_their_change(
    engine: AsyncEngine,
) -> None:
    connections = SealedConnections(CredentialStore(engine, SOURCE_BOX))
    for round_ in range(5):
        await connections.save(1, CHANGE, NOW)
        before = await connections.webhook_secret(1, WXCC)

        await asyncio.gather(
            connections.set_registration(1, Registration.GONE, NOW),
            connections.set_inbound_enabled(1, False, NOW),
            connections.rotate_webhook_secret(1, NOW + timedelta(minutes=round_ + 1)),
        )

        shown = await connections.get(1)
        held = await connections.webhook_secrets(1, WXCC)
        assert shown is not None
        assert shown.registration is Registration.GONE
        assert not shown.inbound_enabled
        assert held is not None
        assert held.previous == before  # the rotation was not overwritten by the other two
        await connections.delete(1)


async def test_a_change_of_a_record_that_is_gone_stores_nothing(engine: AsyncEngine) -> None:
    store = CredentialStore(engine, SOURCE_BOX)
    connections = SealedConnections(store)

    await connections.set_registration(7, Registration.GONE, NOW)
    await connections.set_inbound_enabled(7, False, NOW)
    await connections.rotate_webhook_secret(7, NOW)

    assert await connections.get(7) is None


async def test_the_sweep_does_not_remove_a_secret_whose_record_was_rotated_again(
    engine: AsyncEngine,
) -> None:
    connections = SealedConnections(CredentialStore(engine, SOURCE_BOX))
    await connections.save(1, CHANGE, NOW)
    await connections.rotate_webhook_secret(1, NOW)

    # The sweep found an ended previous secret; the record is then rotated again, so the
    # previous secret it holds now is inside its overlap and must stay.
    await connections.rotate_webhook_secret(1, NOW + timedelta(minutes=59))
    removed = await connections.drop_expired_previous(
        [1], NOW + timedelta(minutes=60), timedelta(minutes=60)
    )

    assert removed == 0
    shown = await connections.get(1)
    assert shown is not None
    assert shown.has_previous_webhook_secret

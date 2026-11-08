"""SQL queries of alert targets and deliveries (ADR 0082 point 9)."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.seed import seed_demo
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.alert_model import AlertChannelKind, AlertTarget, DeliveryState
from tests.support.domain import NOW


async def test_targets_and_deliveries_round_trip_and_go_together(engine: AsyncEngine) -> None:
    await seed_demo(engine)
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        demo = await tx.get_organisation("demo")
    assert demo is not None
    async with uow.locked() as tx:
        org_target = await tx.add_alert_target(
            AlertTarget(None, demo.id, AlertChannelKind.EMAIL, "ops@voorbeeld.nl", "outage")
        )
        platform = await tx.add_alert_target(
            AlertTarget(None, None, AlertChannelKind.WEBEX_SPACE, "Y2lzY29zcGFyazovL3Vz", "warning")
        )
        await tx.save_alert_delivery(DeliveryState(org_target, "a|demo||", "tok", NOW, 2))
    async with uow.read() as tx:
        assert [t.id for t in await tx.alert_targets_of(demo.id)] == [org_target]
        assert [t.id for t in await tx.alert_targets_of(None)] == [platform]
        assert len(await tx.all_alert_targets()) == 2
        (state,) = await tx.alert_delivery_states()
    assert (state.token, state.sent_at, state.attempts) == ("tok", NOW, 2)
    async with uow.locked() as tx:
        await tx.delete_alert_target(org_target)
    async with uow.read() as tx:
        assert await tx.alert_delivery_states() == []
        assert [t.id for t in await tx.all_alert_targets()] == [platform]


async def test_deleting_an_organisation_deletes_its_sealed_records(engine: AsyncEngine) -> None:
    """A later organisation with a reused id never inherits the bot (ADR 0084 point 4) nor the
    contact-center connection and its secrets (ADR 0032 point 9)."""
    from sqlalchemy import select

    from app.adapters.db.models import IntegrationCredentialRow, OrganisationRow
    from app.adapters.db.uow import (
        bot_credential_name,
        contact_center_credential_name,
        organisation_tokens_name,
    )
    from app.domain.scheduling import Organisation

    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        stored = await tx.add_organisation(Organisation(0, "Weg", "weg"))
    async with uow.locked() as tx:
        names = (
            bot_credential_name(stored.id),
            contact_center_credential_name(stored.id),
            organisation_tokens_name(stored.id),
        )
        for name in names:
            tx._session.add(  # type: ignore[attr-defined]  # noqa: SLF001
                IntegrationCredentialRow(name=name, nonce=b"n", ciphertext=b"c", updated_at=NOW)
            )
    async with uow.locked() as tx:
        await tx.delete_organisation(stored.id)
    async with uow.read() as tx:
        session = tx._session  # type: ignore[attr-defined]  # noqa: SLF001
        assert await session.get(IntegrationCredentialRow, bot_credential_name(stored.id)) is None
        for name in names:
            assert await session.get(IntegrationCredentialRow, name) is None
        assert (await session.scalars(select(OrganisationRow.id))).all() == []

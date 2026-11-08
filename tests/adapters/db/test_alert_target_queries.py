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
        assert [t.id for t in await tx.list_alert_targets(demo.id)] == [org_target]
        assert [t.id for t in await tx.list_alert_targets(None)] == [platform]
        assert len(await tx.all_alert_targets()) == 2
        (state,) = await tx.list_alert_deliveries()
    assert (state.token, state.sent_at, state.suppressed) == ("tok", NOW, 2)
    async with uow.locked() as tx:
        await tx.delete_alert_target(org_target)
    async with uow.read() as tx:
        assert await tx.list_alert_deliveries() == []
        assert [t.id for t in await tx.all_alert_targets()] == [platform]

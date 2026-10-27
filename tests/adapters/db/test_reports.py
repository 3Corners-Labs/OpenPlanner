from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.reports import ReportStore, StoredReport
from tests.support.domain import NOW


async def test_a_report_is_stored_and_replaced(engine: AsyncEngine) -> None:
    store = ReportStore(engine)

    assert await store.get("webhook_health") is None
    await store.put("webhook_health", {"deliveries": []}, NOW)
    await store.put("webhook_health", {"deliveries": [{"endpoint": "webex"}]}, NOW)

    assert await store.get("webhook_health") == StoredReport(
        {"deliveries": [{"endpoint": "webex"}]}, NOW
    )

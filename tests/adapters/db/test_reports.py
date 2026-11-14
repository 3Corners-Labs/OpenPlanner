from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.db.probes import ProbeReports
from app.adapters.db.reports import ReportStore, StoredReport
from app.adapters.diagnostics import CheckResult
from app.adapters.service_app_check import SERVICE_APP_REPORT, store_service_app_check
from app.domain.calls import EventKind
from app.domain.guest_access import GUEST_PROBE
from tests.support.domain import NOW


async def test_a_report_is_stored_and_replaced(engine: AsyncEngine) -> None:
    store = ReportStore(engine)

    assert await store.get("webhook_health") is None
    await store.put("webhook_health", {"deliveries": []}, NOW)
    await store.put("webhook_health", {"deliveries": [{"endpoint": "webex"}]}, NOW)

    assert await store.get("webhook_health") == StoredReport(
        {"deliveries": [{"endpoint": "webex"}]}, NOW
    )


async def test_the_probe_registry_notes_events_of_the_open_probe_only(engine: AsyncEngine) -> None:
    probes = ProbeReports(ReportStore(engine))

    assert await probes.note("task-1", EventKind.TASK_CREATED, NOW) is False  # nothing open
    await probes.open("task-1", NOW)

    assert await probes.note("task-1", EventKind.TASK_CREATED, NOW) is True
    assert await probes.note("task-2", EventKind.TASK_ENDED, NOW) is False
    assert await probes.note("task-1", EventKind.TASK_ENDED, NOW) is True
    assert await probes.events("task-1") == (EventKind.TASK_CREATED, EventKind.TASK_ENDED)
    assert await probes.events("task-2") == ()


async def test_a_new_probe_replaces_the_previous_one(engine: AsyncEngine) -> None:
    probes = ProbeReports(ReportStore(engine))
    await probes.open("task-1", NOW)
    await probes.note("task-1", EventKind.TASK_CREATED, NOW)

    await probes.open("task-2", NOW)

    assert await probes.events("task-1") == ()
    assert await probes.events("task-2") == ()
    assert await probes.note("task-1", EventKind.TASK_ENDED, NOW) is False


async def test_the_check_is_stored_as_a_report(engine: AsyncEngine) -> None:
    store = ReportStore(engine)

    await store_service_app_check(
        store, [CheckResult("token refresh", None, True, "ok")], True, NOW
    )

    report = await store.get(SERVICE_APP_REPORT)
    assert report is not None
    assert report.checked_at == NOW
    assert [r["name"] for r in report.body["results"]] == [
        line.name for line in [CheckResult("token refresh", None, True, "ok")]
    ]


async def test_the_guest_fact_is_written_by_the_check_and_a_later_check_takes_it_away(
    engine: AsyncEngine,
) -> None:
    store = ReportStore(engine)
    meetings = CheckResult("GET /meetings", 200, True, "ok")
    guest_ok = CheckResult(GUEST_PROBE, 200, True, "ok")
    guest_refused = CheckResult(GUEST_PROBE, 403, False, "scope")

    assert not await store.guest_check_passed()  # no check has run
    await store_service_app_check(store, [meetings, guest_ok], False, NOW)
    assert await store.guest_check_passed()
    await store_service_app_check(store, [meetings, guest_refused], False, NOW)
    assert not await store.guest_check_passed()  # a revoked scope stops new choices of embedded
    await store_service_app_check(store, [meetings, guest_ok], False, NOW)
    assert await store.guest_check_passed()
    await store_service_app_check(store, [meetings], False, NOW)  # run with the switch off
    assert not await store.guest_check_passed()


async def test_a_report_without_the_fact_does_not_count(engine: AsyncEngine) -> None:
    store = ReportStore(engine)

    await store.put(SERVICE_APP_REPORT, {"results": [{"name": GUEST_PROBE, "ok": True}]}, NOW)

    assert not await store.guest_check_passed()  # a line is no fact; the next check writes one

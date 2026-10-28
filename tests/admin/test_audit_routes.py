"""The audit log page (ADR 0031): exact per level, system events, filters, reasons."""

from datetime import timedelta

import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site
from tests.support.domain import NOW

GLOBAL_FAILURE = AuditEvent(
    NOW - timedelta(minutes=4),
    SYSTEM_ACTOR,
    "job.purge_pii",
    Outcome.FAILURE,
    "global_marker",
    source=Source.SYSTEM,
)
ORG_CHANGE = AuditEvent(
    NOW - timedelta(minutes=3),
    "sub-org",
    "holidays.assign",
    Outcome.SUCCESS,
    "org_marker",
    "demo",
)
GROUP_RETRY = AuditEvent(
    NOW - timedelta(minutes=2),
    SYSTEM_ACTOR,
    "job.send_reminder",
    Outcome.RETRY,
    "smtp_unreachable",
    "demo",
    "demo",
    "attempt:2,channel:sms",
    Source.SYSTEM,
    7,
)
OTHER_GROUP = AuditEvent(
    NOW - timedelta(minutes=1),
    SYSTEM_ACTOR,
    "job.send_confirmation",
    Outcome.SUCCESS,
    "other_marker",
    "andere",
    "andere-team",
    "",
    Source.SYSTEM,
    8,
)


@pytest.fixture
async def logged(site: Site) -> Site:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        for event in (GLOBAL_FAILURE, ORG_CHANGE, GROUP_RETRY, OTHER_GROUP):
            await tx.add_audit_event(event)
    return site


@pytest.mark.parametrize(
    ("principal", "visible"),
    [
        (SUPER, {"global_marker", "org_marker", "smtp_unreachable", "other_marker"}),
        (ORG_ADMIN, {"org_marker", "smtp_unreachable"}),
        (GROUP_ADMIN, {"smtp_unreachable"}),
        (OUTSIDER, {"other_marker"}),
    ],
)
async def test_each_role_sees_its_own_level_and_nothing_above_or_beside(
    logged: Site, principal: object, visible: set[str]
) -> None:
    await logged.login_as(principal)  # type: ignore[arg-type]
    text = (await logged.client.get("/admin/audit")).text

    markers = {"global_marker", "org_marker", "smtp_unreachable", "other_marker"}
    assert {m for m in markers if m in text} == visible


async def test_a_system_event_explains_itself(logged: Site) -> None:
    await logged.login_as(GROUP_ADMIN)
    text = (await logged.client.get("/admin/audit")).text

    assert "Herinnering versturen" in text
    assert "Wordt opnieuw geprobeerd" in text
    assert "<code>smtp_unreachable</code>" in text
    assert "poging 2, sms" in text
    assert "<td>Systeem</td>" in text
    assert 'href="/admin/groups/demo/bookings/7"' in text


async def test_a_denial_shows_its_reason(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/groups/andere-team")
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/audit")).text

    assert "Buiten de eigen organisatie of groep." in text
    assert "<code>out_of_scope</code>" in text


@pytest.mark.parametrize(
    ("params", "visible"),
    [
        ({"source": "system"}, {"global_marker", "smtp_unreachable", "other_marker"}),
        ({"source": "admin"}, {"org_marker"}),
        ({"problems": "1"}, {"global_marker", "smtp_unreachable"}),
        ({"group": "demo"}, {"smtp_unreachable"}),
        ({"booking": "8"}, {"other_marker"}),
        # Unknown or malformed values are ignored, never an error.
        ({"source": "x", "group": "", "booking": "abc", "problems": "yes"}, None),
    ],
)
async def test_filters_narrow_the_list(
    logged: Site, params: dict[str, str], visible: set[str] | None
) -> None:
    await logged.login_as(SUPER)
    response = await logged.client.get("/admin/audit", params=params)

    markers = {"global_marker", "org_marker", "smtp_unreachable", "other_marker"}
    assert response.status_code == 200
    assert {m for m in markers if m in response.text} == (visible or markers)


async def test_a_filter_cannot_reach_outside_the_scope(logged: Site) -> None:
    await logged.login_as(GROUP_ADMIN)

    text = (await logged.client.get("/admin/audit", params={"group": "andere-team"})).text
    by_booking = (await logged.client.get("/admin/audit", params={"booking": "8"})).text

    assert "other_marker" not in text
    assert "smtp_unreachable" in text  # an out-of-scope group is ignored, not applied
    assert "other_marker" not in by_booking


async def test_a_storage_event_explains_itself_to_the_superuser_only(site: Site) -> None:
    # ADR 0040: the storage watch writes global events.
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.add_audit_event(
            AuditEvent(
                NOW,
                SYSTEM_ACTOR,
                "storage.check",
                Outcome.FAILURE,
                "free_space_low",
                detail="free_percent:12,database_mb:340",
                source=Source.SYSTEM,
            )
        )

    await site.login_as(SUPER)
    text = (await site.client.get("/admin/audit")).text
    await site.login_as(ORG_ADMIN)
    hidden = (await site.client.get("/admin/audit")).text

    assert "Opslag controleren" in text
    assert "Er is weinig schijfruimte over voor de gegevens." in text
    assert "12% vrij, database 340 MB" in text
    assert "free_space_low" not in hidden

"""The audit log page (ADR 0031): exact per level, system events, filters, reasons."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Category, Level, Outcome, Source
from app.domain.authorization import Scope
from app.domain.diagnostics import DiagnosticLog
from app.domain.event_context import TRACE_ID
from app.domain.event_schema import rfc3339
from tests.admin.conftest import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, Site
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


@pytest.fixture
async def traced(site: Site) -> Site:
    """The four events of ``logged``; the group's retry has a trace id."""
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        for event in (GLOBAL_FAILURE, ORG_CHANGE, OTHER_GROUP):
            await tx.add_audit_event(event)
        await tx.add_audit_event(replace(GROUP_RETRY, trace_id="c" * 32))
    return site


@pytest.mark.parametrize(
    ("principal", "visible"),
    [
        (SUPER, {"global_marker", "org_marker", "smtp_unreachable", "other_marker"}),
        (ORG_ADMIN, {"org_marker", "smtp_unreachable"}),
        (GROUP_ADMIN, {"smtp_unreachable"}),
        (OUTSIDER, {"other_marker"}),
        # ADR 0039: the auditor reads exactly what the organisation admin reads.
        (AUDITOR, {"org_marker", "smtp_unreachable"}),
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


async def test_the_auditor_sees_the_booking_id_but_no_link_to_the_booking(logged: Site) -> None:
    # ADR 0039 B6: the timeline needs bookings.view, which shows personal data.
    await logged.login_as(AUDITOR)
    text = (await logged.client.get("/admin/audit")).text

    assert "smtp_unreachable" in text
    assert 'href="/admin/groups/demo/bookings/7"' not in text


async def test_a_denial_shows_its_reason(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/groups/andere-team")
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/audit")).text

    assert "Buiten de eigen organisatie of groep." in text
    assert "<code>out_of_scope</code>" in text


async def test_events_of_a_request_carry_its_trace_and_show_utc(site: Site) -> None:
    # ADR 0040, part 2: one trace per request, the web service, the level from the outcome.
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/groups/andere-team")
    await site.client.get("/admin/groups/andere-team")
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        entries = await tx.list_audit_events(Scope(everything=True))

    denials = [entry.event for entry in entries if entry.event.outcome is Outcome.DENIED]
    assert len(denials) == 2
    assert all(TRACE_ID.fullmatch(event.trace_id) for event in denials)
    assert denials[0].trace_id != denials[1].trace_id
    assert {(event.service, event.level) for event in denials} == {("web", Level.WARNING)}

    await site.login_as(SUPER)
    text = (await site.client.get("/admin/audit")).text
    assert f'<time datetime="{rfc3339(denials[0].at)}">' in text


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


async def test_a_security_event_explains_itself_to_the_superuser_only(site: Site) -> None:
    # ADR 0034, AU-4: rejected webhooks are global, with the number held back.
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.add_audit_event(
            AuditEvent(
                NOW,
                SYSTEM_ACTOR,
                "webhook.rejected",
                Outcome.DENIED,
                "invalid_signature",
                detail="source:wxcc,suppressed:41",
                source=Source.SYSTEM,
                category=Category.SECURITY,
            )
        )

    await site.login_as(SUPER)
    text = (await site.client.get("/admin/audit")).text
    await site.login_as(ORG_ADMIN)
    hidden = (await site.client.get("/admin/audit")).text

    assert "Webhook geweigerd" in text
    assert "Handtekening of tijdstempel klopt niet." in text
    assert "van Webex Contact Center, nog 41 keer sinds de vorige melding" in text
    assert "invalid_signature" not in hidden


async def test_an_admin_action_is_shipped_once_as_an_audit_line(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    # ADR 0034, AU-1: after commit, one JSON line per event with log_type audit, subject only.
    capsys.readouterr()
    await site.login_as(GROUP_ADMIN)
    await site.client.get("/admin/groups/andere-team")

    lines = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    shipped = [line for line in lines if line.get("log_type") == "audit"]
    denied = [line for line in shipped if line["outcome"] == "denied"]
    assert len(denied) == 1
    assert denied[0]["schema"] == "openplanner.event/1"
    assert denied[0]["service.name"] == "web"
    assert denied[0]["actor"] == GROUP_ADMIN.subject
    assert len({line["event_id"] for line in shipped}) == len(shipped)


@pytest.mark.parametrize(
    ("params", "visible"),
    [
        ({"level": "error"}, {"global_marker"}),
        ({"level": "warning"}, {"global_marker", "smtp_unreachable"}),
        ({"category": "audit"}, {"org_marker"}),
        ({"category": "system"}, {"global_marker", "smtp_unreachable", "other_marker"}),
        ({"trace": "c" * 32}, {"smtp_unreachable"}),
        # ADR 0040: days in ADMIN_TIMEZONE; NOW is 2026-01-01 09:00 in Amsterdam.
        ({"from": "2026-01-02"}, set()),
        ({"to": "2025-12-31"}, set()),
        ({"from": "2026-01-01", "to": "2026-01-01"}, None),
        # Malformed values are ignored, never an error.
        ({"level": "loud", "trace": "x", "from": "yesterday", "to": "2026-13-01"}, None),
    ],
)
async def test_level_category_period_and_trace_narrow_the_list(
    traced: Site, params: dict[str, str], visible: set[str] | None
) -> None:
    await traced.login_as(SUPER)
    response = await traced.client.get("/admin/audit", params=params)

    assert response.status_code == 200
    markers = {"global_marker", "org_marker", "smtp_unreachable", "other_marker"}
    expected = markers if visible is None else visible
    assert {m for m in markers if m in response.text} == expected


async def test_each_event_links_to_its_whole_chain(traced: Site) -> None:
    await traced.login_as(SUPER)
    text = (await traced.client.get("/admin/audit")).text

    assert f'href="/admin/audit?trace={"c" * 32}"' in text


async def test_the_export_is_the_current_filter_inside_the_scope(traced: Site) -> None:
    await traced.login_as(ORG_ADMIN)
    page = (await traced.client.get("/admin/audit", params={"category": "system"})).text
    assert 'href="/admin/audit/export.csv?category=system"' in page

    response = await traced.client.get("/admin/audit/export.csv", params={"category": "system"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith('attachment; filename="audit-')
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith("﻿".encode())
    text = response.content.decode("utf-8")
    assert "smtp_unreachable" in text
    assert "org_marker" not in text  # filtered out: category audit
    assert "other_marker" not in text  # outside the scope
    assert "global_marker" not in text


async def test_every_export_is_itself_an_audit_event(traced: Site) -> None:
    await traced.login_as(AUDITOR)
    await traced.client.get("/admin/audit/export.csv")
    await traced.login_as(SUPER)

    text = (await traced.client.get("/admin/audit", params={"category": "audit"})).text

    assert "Auditlog exporteren" in text


async def test_the_group_admin_may_view_but_not_export(traced: Site) -> None:
    await traced.login_as(GROUP_ADMIN)
    page = (await traced.client.get("/admin/audit")).text
    response = await traced.client.get("/admin/audit/export.csv")

    assert "export.csv" not in page
    assert response.status_code == 403


async def test_an_export_over_the_period_limit_is_refused_on_the_page(traced: Site) -> None:
    await traced.login_as(SUPER)

    response = await traced.client.get(
        "/admin/audit/export.csv", params={"from": "2024-01-01", "to": "2026-01-01"}
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "De export beslaat te veel dagen." in response.text


async def test_the_superuser_switches_diagnostic_mode_and_the_group_sees_its_debug_lines(
    site: Site,
) -> None:
    # ADR 0040, part 4: superuser only; the group admin reads the debug lines of their group.
    await site.login_as(SUPER)
    page = (await site.client.get("/admin/audit")).text
    assert "De diagnosemodus staat nergens aan." in page

    switched = await site.client.post("/admin/audit/diagnostics", data={"target": "group:demo"})
    assert switched.status_code == 303
    assert switched.headers["location"] == "/admin/audit?done=diagnostics_on#diagnostics"
    page = (await site.client.get(switched.headers["location"])).text
    assert "De diagnosemodus staat aan." in page
    assert 'name="target" value="group:demo"' in page

    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    log = DiagnosticLog(SqlUnitOfWork(app.state.engine), app.state.clock)
    assert await log.debug("job.run", "demo", "demo", detail="type:send_reminder")

    await site.login_as(GROUP_ADMIN)
    page = (await site.client.get("/admin/audit", params={"category": "diagnostic"})).text
    assert "Taak uitgevoerd (diagnose)" in page
    assert "Diagnosemodus" not in page  # the switch is the superuser's

    await site.login_as(SUPER)
    off = await site.client.post("/admin/audit/diagnostics/off", data={"target": "group:demo"})
    assert off.headers["location"] == "/admin/audit?done=diagnostics_off#diagnostics"
    assert not await log.debug("job.run", "demo", "demo")


async def test_an_unknown_diagnostics_target_changes_nothing(site: Site) -> None:
    await site.login_as(SUPER)

    response = await site.client.post("/admin/audit/diagnostics", data={"target": "group:x"})

    assert response.headers["location"] == "/admin/audit?done=unchanged#diagnostics"

"""The CSV exports of the usage reports: scope, files, headers and audit (ADR 0117 step 8)."""

import csv
import hashlib
import io
from typing import Any

import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.admin.usage_routes import EXPORT_REFUSALS
from app.domain.audit import Outcome
from app.domain.authorization import Scope
from app.domain.csv_export import ExportLimits, ExportRefusal
from tests.admin.conftest import AGENT, AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, Site
from tests.support.usage_data import DAY
from tests.support.usage_data import fill as _fill

GROUP = f"/admin/groups/demo/usage/export.csv?from={DAY}&to={DAY}"


def _rows(text: str) -> list[list[str]]:
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:], newline=""), delimiter=";"))


async def _exports(site: Site) -> list[Any]:
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        entries = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [e.event for e in reversed(entries) if e.event.action == "reports.export"]


async def test_a_group_admin_downloads_the_screen_as_csv(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(GROUP)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="usage-volume-demo-20260101-20260101'
    )
    rows = _rows(response.text)
    assert rows[0] == ["table", "row", "column", "value", "note"]
    assert ["days", DAY, "count", "12", ""] in rows


async def test_the_checksum_header_is_the_sha256_of_the_file_and_the_audit_event_keeps_it(
    site: Site,
) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(GROUP)
    (event,) = await _exports(site)

    digest = hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-content-sha256"] == digest
    assert event.outcome is Outcome.SUCCESS
    assert (event.organisation, event.group) == ("demo", "demo")
    assert f"sha256:{digest}" in event.detail
    assert "view:volume" in event.detail
    assert "rows:" in event.detail


async def test_the_file_holds_the_small_cell_rule_of_the_screen(site: Site) -> None:
    await _fill(site, "demo", 3)
    await site.login_as(GROUP_ADMIN)

    rows = _rows((await site.client.get(GROUP)).text)

    assert ["days", DAY, "count", "", "hidden"] in rows
    assert not any(row[3] == "3" for row in rows)


async def test_the_tidy_file_has_the_documented_columns_and_one_row_per_hourly_cell(
    site: Site,
) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(
        f"/admin/groups/demo/usage/export.csv?view=tidy&from={DAY}&to={DAY}"
    )

    rows = _rows(response.text)
    assert rows[0][:4] == ["hour", "organisation", "group", "metric"]
    assert rows[0][-3:] == ["count", "sum", "max"]
    assert ["2026-01-01T08:00:00Z", "demo", "demo", "U1"] in [r[:4] for r in rows]
    assert "openplanner.usage-1" in response.headers["content-disposition"]


async def test_the_organisation_export_adds_its_groups_up_for_an_organisation_admin(
    site: Site,
) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(ORG_ADMIN)

    response = await site.client.get(
        f"/admin/organisations/demo/usage/export.csv?from={DAY}&to={DAY}"
    )

    assert response.status_code == 200
    assert ["days", DAY, "count", "12", ""] in _rows(response.text)


async def test_the_auditor_may_export_and_the_agent_may_not(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(AUDITOR)
    assert (
        await site.client.get(f"/admin/organisations/demo/usage/export.csv?from={DAY}&to={DAY}")
    ).status_code == 200

    await site.login_as(AGENT)
    assert (await site.client.get(GROUP)).status_code == 403


async def test_another_organisation_cannot_be_exported(site: Site) -> None:
    await _fill(site, "demo", 12)
    await site.login_as(OUTSIDER)

    group = await site.client.get(GROUP)
    organisation = await site.client.get(
        f"/admin/organisations/demo/usage/export.csv?from={DAY}&to={DAY}"
    )

    assert group.status_code == 403
    assert organisation.status_code == 403
    assert "text/html" in group.headers["content-type"] + organisation.headers["content-type"]
    assert "x-content-sha256" not in group.headers
    assert "x-content-sha256" not in organisation.headers


LONG = "from=2025-12-01&to=2026-01-31"
"""A period of 62 days, over the limit of 31 the refusal tests set."""


@pytest.mark.parametrize(
    ("export", "report"),
    [
        (
            f"/admin/groups/demo/usage/export.csv?{LONG}",
            f"/admin/groups/demo/usage?view=volume&{LONG}",
        ),
        (
            f"/admin/organisations/demo/usage/export.csv?view=queue&{LONG}",
            f"/admin/organisations/demo/usage?view=queue&{LONG}",
        ),
        (
            f"/admin/organisations/demo/usage/export.csv?view=tidy&{LONG}",
            f"/admin/organisations/demo/usage?view=volume&{LONG}",
        ),
    ],
)
async def test_an_export_over_the_day_limit_goes_back_to_the_report_with_the_reason(
    site: Site, export: str, report: str
) -> None:
    # Post/redirect/get: the refusal is never a page at the export's own address, where a
    # reload, a language switch or a login would run the export again.
    await site.login_as(ORG_ADMIN)
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.state.usage_export_service._limits = lambda: ExportLimits(100, 31)  # noqa: SLF001

    response = await site.client.get(export)
    (event,) = await _exports(site)

    assert response.status_code == 303
    assert response.headers["location"] == f"{report}&refused=too_many_days"
    page = await site.client.get(response.headers["location"])
    assert page.status_code == 200
    assert "De export beslaat te veel dagen." in page.text
    assert (event.outcome, event.reason) == (Outcome.FAILURE, "too_many_days")
    assert "sha256" not in event.detail


@pytest.mark.parametrize("refused", ["zz_marker", "x" * 200])
async def test_an_unknown_refusal_code_on_a_report_shows_nothing_and_is_not_echoed(
    site: Site, refused: str
) -> None:
    await site.login_as(ORG_ADMIN)

    for path in ("/admin/groups/demo/usage", "/admin/organisations/demo/usage"):
        page = await site.client.get(path, params={"refused": refused})

        assert page.status_code == 200
        assert refused not in page.text
        assert 'class="error-message"' not in page.text


def test_every_refusal_code_has_a_text_on_the_report() -> None:
    assert set(EXPORT_REFUSALS) == set(ExportRefusal)


async def test_an_unknown_view_is_refused_and_never_echoed(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get("/admin/groups/demo/usage/export.csv?view=<script>")

    assert response.status_code == 400
    assert "<script>" not in response.text


async def test_the_report_pages_offer_the_downloads_only_to_who_may_export(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    page = (await site.client.get(f"/admin/groups/demo/usage?from={DAY}&to={DAY}")).text

    assert "export.csv?view=volume" in page
    assert "export.csv?view=tidy" in page

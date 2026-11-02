"""What leaves the admin portal as a file carries no personal data and no other tenant (WP-B2).

The page sweep (``test_privacy_sweep``) proves that no list, overview or alert page shows a
personal-data marker. This file does the same for everything that is a download, and for the
narrowing parameters of the admin lists (ASVS V8, TEN-01, TEN-02):

* the audit export (CSV): every role that may export gets only its own scope, and no marker;
* the configuration package (ADR 0089): an installation export for the superuser. By design it
  holds every organisation and no state, so the test asserts exactly that: no personal data,
  no staff, no audit log, and no secret in clear text, but both organisations;
* a ``group`` or ``organisation`` in a query narrows a list, it never widens it.

The downloads are discovered from the app (``DOWNLOAD_MODULES`` in ``test_download_headers``
pins the places that set the header), so a new export has to be added to ``EXPORTS`` here.
"""

import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from app.adapters.db.configuration_package import inspect_package
from app.domain.audit import AuditEvent, Outcome
from app.domain.configuration_package import CONFIGURATION_TABLES, STATE_TABLES
from tests.admin.conftest import AUDITOR, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.admin.test_download_headers import DOWNLOAD_MODULES
from tests.admin.test_privacy_sweep import (  # noqa: F401 - fixtures are collected by name
    LOG_DESTINATION,
    MARKERS,
    Fixture,
    _leaks,
    _uow,
    marked,
    settings,
)
from tests.admin.test_settings_routes import PACKAGE_PASSPHRASE

DEMO_MARKER = "wpb2demomarker"
OTHER_MARKER = "wpb2andere"
"""Details of audit events inside one organisation each; the other must never see them."""
STAFF_NAME = "Gina"
"""A staff member of ``demo`` (``GROUP_ADMIN``): state, not configuration."""
CLEAR_SECRETS = (LOG_DESTINATION["secret"],)
"""Secrets the sweep stored through the portal; none may be readable in a package."""

EXPORTS = {
    "admin/audit_routes.py": "the audit export, per role below",
    "admin/branding_routes.py": "theme.css: colours of the organisation, swept as a page",
    "admin/idp_mapping_routes.py": "the claim mapping text, swept as a page",
    "admin/settings_routes.py": "the configuration package, below",
    "api/agent/routes.py": "the machine API, tests/api/test_privacy_sweep_api.py",
}


def test_every_place_that_sets_a_download_is_swept() -> None:
    assert set(EXPORTS) == DOWNLOAD_MODULES, "a new download needs a sweep: add it to EXPORTS"


@pytest.fixture
async def audited(marked: tuple[Site, Fixture]) -> Site:  # noqa: F811
    """The marker site with one audit event inside ``demo`` and one inside ``andere``."""
    site, _ = marked
    now = site.clock.now() - timedelta(minutes=1)
    events = (
        AuditEvent(now, "sub-org", "holidays.assign", Outcome.SUCCESS, DEMO_MARKER, "demo"),
        AuditEvent(now, "sub-outsider", "holidays.assign", Outcome.SUCCESS, OTHER_MARKER, "andere"),
    )
    async with _uow(site).locked() as tx:
        for event in events:
            await tx.add_audit_event(event)
    return site


async def _export(site: Site, principal: object, **query: str) -> str:
    await site.login_as(principal)  # type: ignore[arg-type]
    response = await site.client.get("/admin/audit/export.csv", params=query)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    return response.content.decode("utf-8")


@pytest.mark.parametrize(
    ("who", "sees", "never"),
    [
        (SUPER, {DEMO_MARKER, OTHER_MARKER}, set()),
        (ORG_ADMIN, {DEMO_MARKER}, {OTHER_MARKER}),
        (AUDITOR, {DEMO_MARKER}, {OTHER_MARKER}),
        (OUTSIDER, {OTHER_MARKER}, {DEMO_MARKER}),
    ],
    ids=["superuser", "org_admin", "auditor", "other_organisation"],
)
async def test_the_audit_export_holds_no_personal_data_and_only_its_own_scope(
    audited: Site, who: object, sees: set[str], never: set[str]
) -> None:
    text = await _export(audited, who)

    assert all(marker in text for marker in sees), "the export is empty: the sweep proves nothing"
    assert not any(marker in text for marker in never), "an event of the other organisation"
    assert not _leaks(text, set()), "a personal-data marker in the audit export (ADR 0063 §3)"
    assert STAFF_NAME not in text  # the actor is a subject id, never a name


@pytest.mark.parametrize("query", [{"group": "demo"}, {"group": "andere-team"}])
async def test_a_group_in_the_query_cannot_widen_the_audit_export(
    audited: Site, query: dict[str, str]
) -> None:
    text = await _export(audited, OUTSIDER, **query)

    assert DEMO_MARKER not in text
    assert not _leaks(text, set())


@pytest.mark.parametrize(
    "query", ["organisation=demo", "group=demo", "organisation=demo&group=demo"]
)
async def test_a_query_cannot_make_the_staff_list_show_another_organisation(
    audited: Site, query: str
) -> None:
    await audited.login_as(SUPER)
    assert STAFF_NAME in (await audited.client.get(f"/admin/staff?{query}")).text, "no control"
    await audited.login_as(OUTSIDER)

    page = (await audited.client.get(f"/admin/staff?{query}")).text

    assert STAFF_NAME not in page, "a staff member of the other organisation"
    assert not _leaks(page, set())


async def _package(site: Site) -> bytes:
    await site.login_as(SUPER)
    response = await site.client.post(
        "/admin/settings/package/export",
        data={"passphrase": PACKAGE_PASSPHRASE, "passphrase_repeat": PACKAGE_PASSPHRASE},
        headers=SAME_ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.content


async def test_the_configuration_package_holds_no_personal_data_no_state_and_no_clear_secret(
    audited: Site, tmp_path: Path
) -> None:
    package = await _package(audited)

    # ADR 0089 points 2 and 3: the PII fields are on bookings, a state table, which is emptied
    # and vacuumed; staff and the audit log are state too; secrets are sealed.
    assert not _leaks(package.decode("latin-1"), set()), "personal data in the package"
    for secret in CLEAR_SECRETS:
        assert secret.encode() not in package, "a secret in clear text in the package"
    for name in (STAFF_NAME, DEMO_MARKER, OTHER_MARKER):
        assert name.encode() not in package, f"state in the package: {name}"
    # Not vacuous: the package opens with the passphrase, and the sealed secret is in it.
    summary = inspect_package(package, PACKAGE_PASSPHRASE, tmp_path)
    assert summary.secrets >= 1
    assert summary.counts["organisations"] == 2, "an installation export holds every organisation"


async def test_every_state_table_of_the_package_is_empty_and_every_other_table_is_configuration(
    audited: Site, tmp_path: Path
) -> None:
    package = await _package(audited)
    path = tmp_path / "exported.sqlite"
    path.write_bytes(package)

    with closing(sqlite3.connect(path)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608  # nosec B608 - names from sqlite_master
            for table in tables
        }

    state = {table: n for table, n in counts.items() if table in STATE_TABLES and n}
    assert state == {}, "a state table with rows in the package"
    assert counts["organisations"] == 2
    assert CONFIGURATION_TABLES <= tables

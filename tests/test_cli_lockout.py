"""Host commands for an IdP outage or a lockout (ADR 0042 §8, §10): oidc-check and closure."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.adapters.db.engine import create_engine
from app.adapters.db.models import AuditEventRow, ClosureNoticeRow, ClosureRow
from app.cli import CLI_ACTOR, _closure_lift, _closure_start, _oidc_check, main
from app.config import Settings
from app.domain.authorization import Grant
from tests.adapters.db.conftest import seed_group
from tests.adapters.test_oidc_check import _transport
from tests.support.database import migrated_database

NOTICES = {"nl": "Storing bij de aanbieder.", "en": "Outage at our provider."}


@pytest.fixture
def configured(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    return settings.model_copy(
        update={"database_url": migrated_database(tmp_path, monkeypatch, "cli-lockout")}
    )


async def _seed(settings: Settings, *slugs: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        for slug in slugs:
            await seed_group(engine, slug=slug)
    finally:
        await engine.dispose()


async def _rows(settings: Settings) -> tuple[list[ClosureRow], list[AuditEventRow], list[str]]:
    engine = create_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            closures = list((await connection.execute(select(ClosureRow))).all())
            audit = list((await connection.execute(select(AuditEventRow))).all())
            notices = list((await connection.execute(select(ClosureNoticeRow.text))).scalars())
    finally:
        await engine.dispose()
    return closures, audit, notices  # type: ignore[return-value]


# --- closure -------------------------------------------------------------------------------


async def test_closing_a_group_from_the_host_is_audited_as_the_cli(configured: Settings) -> None:
    await _seed(configured, "aangifte")

    report, ok = await _closure_start(
        configured, group="aangifte", organisation=None, notices=NOTICES, ends_at=None
    )

    assert ok, report
    assert report == "closed aangifte: 0 bookings on hold\n"
    closures, audit, notices = await _rows(configured)
    assert len(closures) == 1
    assert closures[0].ends_at is None
    assert sorted(notices) == sorted(NOTICES.values())
    assert [(e.actor, e.action, e.group, e.detail) for e in audit] == [
        ("system:cli", "closure.manage", "aangifte", "closure.start,bookings:0")
    ]

    report, ok = await _closure_lift(configured, group="aangifte", organisation=None)

    assert ok, report
    assert report == "lifted aangifte\n"
    closures, audit, _ = await _rows(configured)
    assert closures[0].ends_at is not None
    assert audit[-1].actor == "system:cli"
    assert audit[-1].detail == "closure.lift,bookings:0"


async def test_closing_an_organisation_closes_each_of_its_groups(configured: Settings) -> None:
    await _seed(configured, "paspoort")

    report, ok = await _closure_start(
        configured,
        group=None,
        organisation="org-paspoort",
        notices=NOTICES,
        ends_at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert ok, report
    assert report == "closed paspoort: 0 bookings on hold\n"
    closures, audit, _ = await _rows(configured)
    assert closures[0].ends_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert [(e.actor, e.action, e.organisation) for e in audit] == [
        ("system:cli", "organisation.closure", "org-paspoort")
    ]

    report, ok = await _closure_lift(configured, group=None, organisation="org-paspoort")
    assert (report, ok) == ("lifted paspoort\n", True)


async def test_the_domain_rules_are_the_portals(configured: Settings) -> None:
    await _seed(configured, "aangifte")

    report, ok = await _closure_start(
        configured, group="aangifte", organisation=None, notices={"nl": "x", "en": ""}, ends_at=None
    )
    assert (report, ok) == ("refused: invalid_configuration\n", False)

    report, ok = await _closure_start(
        configured, group="onbekend", organisation=None, notices=NOTICES, ends_at=None
    )
    assert (report, ok) == ("refused: group_not_found\n", False)

    report, ok = await _closure_lift(configured, group="aangifte", organisation=None)
    assert (report, ok) == ("refused: closure_not_found\n", False)


def test_the_cli_actor_is_no_login() -> None:
    assert CLI_ACTOR.subject == "system:cli"
    assert CLI_ACTOR.grants == frozenset({Grant("superuser")})
    assert CLI_ACTOR.custom_roles == frozenset()


@pytest.mark.parametrize(
    "argv",
    [
        ["closure", "start", "--notice-nl", "a", "--notice-en", "b"],
        [
            "closure",
            "start",
            "--group",
            "g",
            "--organisation",
            "o",
            "--notice-nl",
            "a",
            "--notice-en",
            "b",
        ],
        ["closure", "start", "--group", "g", "--notice-nl", "a"],
        [
            "closure",
            "start",
            "--group",
            "g",
            "--notice-nl",
            "a",
            "--notice-en",
            "b",
            "--end",
            "2030-01-01T10:00",
        ],
        ["closure", "lift"],
    ],
)
def test_the_command_line_needs_one_target_both_notices_and_an_end_with_offset(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2


# --- oidc-check ----------------------------------------------------------------------------


def _oidc(settings: Settings, **update: object) -> Settings:
    return settings.model_copy(
        update={
            "oidc_discovery_url": "https://idp.example.nl/.well-known/openid-configuration",
            "oidc_client_secret": SecretStr("client-secret-never-printed"),
            **update,
        }
    )


async def test_oidc_check_prints_findings_and_the_mapping_with_its_source(
    settings: Settings,
) -> None:
    seen: list[httpx.Request] = []
    configured = _oidc(settings, oidc_role_claim="memberOf")

    report, ok = await _oidc_check(configured, _transport(seen))

    assert ok, report
    assert "OK    discovery" in report
    assert "client-secret-never-printed" not in report
    mapping = {line.split()[0]: line.split()[1:] for line in report.splitlines() if "OIDC_" in line}
    assert mapping["OIDC_ROLE_CLAIM"] == ["memberOf", "env"]
    assert mapping["OIDC_GROUP_CLAIM"] == ["groups", "default"]
    assert mapping["OIDC_ROLE_AUDITOR"] == ["openplanner-auditor", "default"]
    assert mapping["OIDC_ROLE_AGENT"] == ["openplanner-agent", "default"]
    assert all(request.method == "GET" for request in seen)
    assert not [r for r in seen if "authorize" in r.url.path or "token" in r.url.path]


async def test_oidc_check_fails_on_a_broken_provider(settings: Settings) -> None:
    report, ok = await _oidc_check(_oidc(settings), _transport([], discovery_status=500))

    assert not ok
    assert "FAIL  discovery" in report


async def test_oidc_check_without_a_discovery_url(settings: Settings) -> None:
    report, ok = await _oidc_check(settings)

    assert not ok
    assert report == "OIDC_DISCOVERY_URL is not set: the admin portal is disabled.\n"

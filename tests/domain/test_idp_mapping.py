"""The identity provider mapping (ADR 0049 points 4 and 17): overview, export, group IdP value."""

from dataclasses import replace

import pytest

from app.domain.admin import AdminService
from app.domain.audit import Outcome
from app.domain.authorization import Grant, Principal, Role
from app.domain.entitlements import ClaimMode
from app.domain.errors import DomainError, ErrorCode
from app.domain.idp_mapping import (
    EXPORTED_EVENT,
    GROUP_IDP_KEY_CHANGED,
    IdpMappingService,
    MappingSettings,
    export_text,
)
from app.domain.staff import Membership, RefusedEntitlement, StaffMember
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import AUDITOR, GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SUPER, two_organisations
from tests.support.domain import NOW

SETTINGS = MappingSettings(ClaimMode.ENTITLEMENTS, "openplanner_entitlements", "openplanner", 30)


def mapping(uow: InMemoryUnitOfWork) -> IdpMappingService:
    clock = FixedClock(NOW)
    return IdpMappingService(uow, clock, AdminService(uow, clock), lambda: SETTINGS)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    unit = InMemoryUnitOfWork(two_organisations())
    unit.state.staff["o-1"] = StaffMember(
        "o-1",
        "Olaf",
        frozenset({Role.ORG_ADMIN}),
        frozenset({Membership("org_admin", "demo"), Membership("group_admin", "demo", "demo")}),
        NOW,
        NOW,
        NOW,
        id=1,
        refused=(
            RefusedEntitlement("openplanner:org_admin:gem-x", "unknown_organisation", None, NOW),
            RefusedEntitlement("openplanner:agent:demo/nope", "unknown_group", "demo", NOW),
        ),
    )
    return unit


async def test_the_overview_lists_the_exact_values_within_the_scope(
    uow: InMemoryUnitOfWork,
) -> None:
    everything = await mapping(uow).overview(SUPER)
    own = await mapping(uow).overview(ORG_ADMIN)

    assert everything.superuser_value == "openplanner:superuser"
    assert [o.slug for o in everything.organisations] == ["andere", "demo"]
    demo = next(o for o in everything.organisations if o.slug == "demo")
    assert [(line.role, line.value, line.holders) for line in demo.lines] == [
        ("org_admin", "openplanner:org_admin:demo", 1),
        ("auditor", "openplanner:auditor:demo", 0),
    ]
    assert [(line.value, line.holders) for line in demo.groups[0].lines] == [
        ("openplanner:group_admin:demo/demo", 1),
        ("openplanner:agent:demo/demo", 0),
    ]
    assert [(r.value, r.reason, r.organisation) for r in everything.refused] == [
        ("openplanner:agent:demo/nope", "unknown_group", "demo"),
        ("openplanner:org_admin:gem-x", "unknown_organisation", None),
    ]
    # A14: the org admin sees their organisation only, and no refusal naming an unknown one.
    assert own.superuser_value is None
    assert [o.slug for o in own.organisations] == ["demo"]
    assert [r.value for r in own.refused] == ["openplanner:agent:demo/nope"]


async def test_the_export_is_one_value_per_line_and_audited(uow: InMemoryUnitOfWork) -> None:
    text = await mapping(uow).export(ORG_ADMIN)

    assert text.splitlines()[0].startswith(
        "# OpenPlanner entitlements, claim openplanner_entitlements"
    )
    assert "openplanner:org_admin:demo" in text.splitlines()
    assert "openplanner:group_admin:demo/demo" in text.splitlines()
    assert "openplanner:superuser" not in text
    assert "andere" not in text
    exported = [e for e in uow.state.audit if e.action == EXPORTED_EVENT]
    assert [(e.actor, e.outcome, e.organisation, e.detail) for e in exported] == [
        ("o-1", Outcome.SUCCESS, "demo", "organisations:1")
    ]
    assert export_text(await mapping(uow).overview(SUPER)).count("openplanner:superuser") == 1


async def test_the_mapping_page_needs_its_permission(uow: InMemoryUnitOfWork) -> None:
    assert (await mapping(uow).overview(AUDITOR)).organisations
    with pytest.raises(DomainError) as caught:
        await mapping(uow).overview(GROUP_ADMIN)
    assert caught.value.code is ErrorCode.NOT_PERMITTED


async def test_an_org_admin_changes_a_groups_idp_value_with_an_audit_trail(
    uow: InMemoryUnitOfWork,
) -> None:
    assert await mapping(uow).group_holders(ORG_ADMIN, "demo") == 1

    changed = await mapping(uow).change_group_idp_key(ORG_ADMIN, "demo", "burgerzaken")
    again = await mapping(uow).change_group_idp_key(ORG_ADMIN, "demo", "burgerzaken")

    assert changed
    assert not again
    assert uow.state.groups[1].idp_key == "burgerzaken"
    events = [e for e in uow.state.audit if e.action == GROUP_IDP_KEY_CHANGED]
    assert [(e.actor, e.organisation, e.group, e.detail) for e in events] == [
        ("o-1", "demo", "demo", "demo->burgerzaken")
    ]
    overview = await mapping(uow).overview(ORG_ADMIN)
    assert overview.organisations[0].groups[0].lines[0].value == (
        "openplanner:group_admin:demo/burgerzaken"
    )


@pytest.mark.parametrize("value", ["", "Burgerzaken", "a b", "x" * 64, "-x"])
async def test_a_value_outside_the_grammar_is_refused(uow: InMemoryUnitOfWork, value: str) -> None:
    with pytest.raises(DomainError) as caught:
        await mapping(uow).change_group_idp_key(ORG_ADMIN, "demo", value)

    assert (caught.value.code, caught.value.field) == (ErrorCode.INVALID_CONFIGURATION, "idp_key")


async def test_a10_a_value_must_be_unique_within_the_organisation(uow: InMemoryUnitOfWork) -> None:
    uow.state.groups[3] = replace(uow.state.groups[1], id=3, slug="demo-twee", idp_key="twee")

    with pytest.raises(DomainError) as caught:
        await mapping(uow).change_group_idp_key(ORG_ADMIN, "demo", "twee")
    assert caught.value.code is ErrorCode.IDP_KEY_TAKEN

    # The same value in another organisation is no collision: every entitlement names its own.
    assert await mapping(uow).change_group_idp_key(SUPER, "andere-team", "twee")


async def test_a10_a_group_admin_and_an_outsider_may_not_change_the_value(
    uow: InMemoryUnitOfWork,
) -> None:
    for admin in (
        GROUP_ADMIN,
        OUTSIDER,
        Principal("c", "C", frozenset({Grant("custom.9", "demo")})),
    ):
        with pytest.raises(DomainError) as caught:
            await mapping(uow).change_group_idp_key(admin, "demo", "burgerzaken")
        assert caught.value.code is ErrorCode.NOT_PERMITTED
    assert uow.state.groups[1].idp_key == "demo"

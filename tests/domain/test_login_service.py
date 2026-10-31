"""The login service (ADR 0049): claims to grants at login and on every request."""

import pytest

from app.domain.admin import (
    AMBIGUOUS_CLAIMS_EVENT,
    ENTITLEMENT_MISMATCH_EVENT,
    ENTITLEMENT_REFUSED_EVENT,
    AdminService,
)
from app.domain.audit import Outcome
from app.domain.authorization import Grant, Permission, Role, Target, decide
from app.domain.entitlements import ClaimMode, LoginClaims
from app.domain.errors import ErrorCode
from app.domain.login_service import ClaimSettings, LoginRefusedError, LoginService
from app.domain.staff import Membership
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import audit_rows, two_organisations
from tests.support.domain import NOW

PREFIX = "openplanner"


def service(uow: InMemoryUnitOfWork, mode: ClaimMode) -> LoginService:
    admin = AdminService(uow, FixedClock(NOW))
    return LoginService(uow, admin, lambda: ClaimSettings(mode, PREFIX))


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork(two_organisations())


async def test_a_login_resolves_records_and_narrows_the_session_claims(
    uow: InMemoryUnitOfWork,
) -> None:
    claims = LoginClaims(
        "sub-mira",
        "Mira Mixed",
        entitlements=frozenset(
            {
                "openplanner:org_admin:demo",
                "openplanner:auditor:andere",
                "openplanner:org_admin:gem-x",
            }
        ),
    )

    outcome = await service(uow, ClaimMode.ENTITLEMENTS).complete_login(claims)

    assert outcome.principal.grants == {Grant("org_admin", "demo"), Grant("auditor", "andere")}
    assert outcome.session_claims.entitlements == {
        "openplanner:org_admin:demo",
        "openplanner:auditor:andere",
    }
    member = uow.state.staff["sub-mira"]
    assert member.memberships == {
        Membership("org_admin", "demo"),
        Membership("auditor", "andere"),
    }
    assert [(r.value, r.reason, r.organisation) for r in member.refused] == [
        ("openplanner:org_admin:gem-x", "unknown_organisation", None)
    ]
    refused = [row for row in audit_rows(uow) if row[1] == ENTITLEMENT_REFUSED_EVENT]
    assert refused == [
        (
            "sub-mira",
            ENTITLEMENT_REFUSED_EVENT,
            Outcome.FAILURE,
            "openplanner:org_admin:gem-x=unknown_organisation",
            None,
        )
    ]


async def test_every_request_resolves_the_session_claims_afresh(uow: InMemoryUnitOfWork) -> None:
    logins = service(uow, ClaimMode.ENTITLEMENTS)
    claims = LoginClaims("s", "S", entitlements=frozenset({"openplanner:group_admin:demo/demo"}))
    target = Target.for_group("demo", "demo")

    before = await logins.principal_for(claims)
    uow.state.groups[1] = uow.state.groups[1].__class__(
        **{**uow.state.groups[1].__dict__, "idp_key": "team-x"}
    )
    after = await logins.principal_for(claims)

    assert decide(before, Permission.GROUP_VIEW, target).allowed
    assert not decide(after, Permission.GROUP_VIEW, target).allowed  # A12
    assert after.grants == frozenset()


async def test_a3_an_ambiguous_legacy_login_is_refused_and_audited(
    uow: InMemoryUnitOfWork,
) -> None:
    claims = LoginClaims(
        "sub-b",
        "Bea",
        roles=frozenset({Role.ORG_ADMIN, Role.AUDITOR}),
        organisations=frozenset({"demo", "andere"}),
    )

    with pytest.raises(LoginRefusedError) as caught:
        await service(uow, ClaimMode.LEGACY).complete_login(claims)

    assert caught.value.reason == "ambiguous_claims"
    assert caught.value.code is ErrorCode.LOGIN_REFUSED
    assert "sub-b" not in uow.state.staff
    assert [row[:3] for row in audit_rows(uow)] == [
        ("sub-b", AMBIGUOUS_CLAIMS_EVENT, Outcome.DENIED)
    ]


async def test_too_many_recognised_values_fail_closed(uow: InMemoryUnitOfWork) -> None:
    for n in range(70):
        uow.state.organisations[10 + n] = uow.state.organisations[1].__class__(
            10 + n, f"Org {n}", f"org-{n}"
        )
        uow.state.organisation_slugs[10 + n] = f"org-{n}"
    claims = LoginClaims(
        "s", "S", entitlements=frozenset(f"openplanner:auditor:org-{n}" for n in range(70))
    )

    with pytest.raises(LoginRefusedError) as caught:
        await service(uow, ClaimMode.ENTITLEMENTS).complete_login(claims)

    assert caught.value.reason == "too_many_entitlements"


async def test_a13_shadow_mode_grants_legacy_and_audits_the_difference(
    uow: InMemoryUnitOfWork,
) -> None:
    claims = LoginClaims(
        "s",
        "S",
        roles=frozenset({Role.AUDITOR}),
        organisations=frozenset({"demo"}),
        entitlements=frozenset({"openplanner:org_admin:demo"}),
    )

    outcome = await service(uow, ClaimMode.SHADOW).complete_login(claims)

    assert outcome.principal.grants == {Grant("auditor", "demo")}
    mismatch = [row for row in audit_rows(uow) if row[1] == ENTITLEMENT_MISMATCH_EVENT]
    assert [row[3] for row in mismatch] == [
        "legacy_only:auditor:demo;entitlements_only:org_admin:demo"
    ]


async def test_a_dry_run_previews_under_another_prefix(uow: InMemoryUnitOfWork) -> None:
    logins = service(uow, ClaimMode.ENTITLEMENTS)
    claims = LoginClaims("s", "S", entitlements=frozenset({"openplanner-acc:superuser"}))

    assert (await logins.principal_for(claims)).grants == frozenset()  # A8
    assert (await logins.preview(claims, "openplanner-acc")).principal.grants == {
        Grant("superuser")
    }

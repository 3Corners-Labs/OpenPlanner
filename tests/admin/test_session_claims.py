"""The admin session keeps the other role claim values, never the custom roles (ADR 0039)."""

from datetime import timedelta

from starlette.requests import Request

from app.admin.session import current_principal, start_session
from app.domain.authorization import MATRIX, CustomRole, Principal, Role
from tests.support.domain import NOW


def request() -> Request:
    return Request({"type": "http", "session": {}})


def test_claims_round_trip_and_custom_roles_are_not_stored() -> None:
    role = CustomRole(1, Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN], "lead", "L", "L")
    principal = Principal(
        "s",
        "S",
        frozenset(),
        frozenset(),
        frozenset({"demo"}),
        role_claims=frozenset({"lead", "other"}),
        custom_roles=frozenset({role}),
    )
    req = request()
    start_session(req, principal, NOW)

    restored = current_principal(req, NOW, timedelta(hours=8))

    assert restored is not None
    assert restored.role_claims == frozenset({"lead", "other"})
    assert restored.custom_roles == frozenset()


def test_badly_shaped_stored_claims_are_dropped() -> None:
    req = request()
    start_session(req, Principal("s", "S", frozenset(), frozenset(), frozenset()), NOW)
    req.session["principal"]["claims"] = ["ok", "not ok", *[f"c{n}" for n in range(30)]]

    restored = current_principal(req, NOW, timedelta(hours=8))

    assert restored is not None
    assert "not ok" not in restored.role_claims
    assert len(restored.role_claims) <= 20


def test_an_older_session_without_claims_still_works() -> None:
    req = request()
    start_session(
        req, Principal("s", "S", frozenset({Role.AUDITOR}), frozenset(), frozenset()), NOW
    )
    del req.session["principal"]["claims"]

    restored = current_principal(req, NOW, timedelta(hours=8))

    assert restored is not None
    assert restored.role_claims == frozenset()

"""The admin session keeps the login's claims, never the grants or custom roles (ADR 0049)."""

from datetime import timedelta

from starlette.requests import Request

from app.admin.session import current_login, start_session
from app.domain.authorization import Role
from app.domain.entitlements import LoginClaims
from tests.support.domain import NOW


def request() -> Request:
    return Request({"type": "http", "session": {}})


def claims(**overrides: object) -> LoginClaims:
    values: dict[str, object] = {
        "subject": "s",
        "display_name": "S",
        "groups": frozenset({"demo"}),
        "role_claims": frozenset({"lead", "other"}),
        "entitlements": frozenset({"openplanner:group_admin:demo/demo"}),
    }
    values.update(overrides)
    return LoginClaims(**values)  # type: ignore[arg-type]


def test_claims_round_trip() -> None:
    req = request()
    start_session(req, claims(), NOW)

    restored = current_login(req, NOW, timedelta(hours=8))

    assert restored == claims()
    assert "grants" not in req.session["principal"]


def test_badly_shaped_stored_claims_are_dropped() -> None:
    req = request()
    start_session(req, claims(), NOW)
    req.session["principal"]["claims"] = ["ok", "not ok", *[f"c{n}" for n in range(30)]]
    req.session["principal"]["entitlements"] = [f"openplanner:x:{n}" for n in range(100)]

    restored = current_login(req, NOW, timedelta(hours=8))

    assert restored is not None
    assert "not ok" not in restored.role_claims
    assert len(restored.role_claims) <= 20
    assert len(restored.entitlements) == 60


def test_an_older_session_without_claims_still_works() -> None:
    req = request()
    start_session(req, claims(roles=frozenset({Role.AUDITOR})), NOW)
    del req.session["principal"]["claims"]
    del req.session["principal"]["entitlements"]

    restored = current_login(req, NOW, timedelta(hours=8))

    assert restored is not None
    assert restored.roles == frozenset({Role.AUDITOR})
    assert restored.role_claims == frozenset()
    assert restored.entitlements == frozenset()

"""A failing identity provider or database never grants access (ASVS V16.5.3, V16.3.3).

The login is the one gate of the portal. If the provider cannot be reached, its answer is
unusable, or the rights cannot be read, the visitor stays outside, and a refusal that has a
cause leaves an event with its code.
"""

import pytest
from sqlalchemy.exc import OperationalError

from app.adapters.db.uow import SqlTransaction
from app.domain.audit import Outcome
from tests.admin.conftest import GROUP_ADMIN, Site
from tests.admin.test_admin_flow import audit_events


async def _is_signed_in(site: Site) -> bool:
    return (await site.client.get("/admin/")).status_code == 200


async def _locked(_self: SqlTransaction) -> None:
    raise OperationalError("SELECT", {}, Exception("database is locked"))


@pytest.mark.parametrize("code", ["discovery_failed", "token_exchange_failed", "invalid_token"])
async def test_an_identity_provider_that_fails_grants_no_session_and_leaves_an_event(
    site: Site, code: str
) -> None:
    await site.client.get("/admin/login")
    site.idp.fail = code

    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.status_code == 401
    assert ("anonymous", "admin.login", Outcome.DENIED, code) in await audit_events(site)
    assert not await _is_signed_in(site)


async def test_a_database_fault_while_resolving_the_rights_grants_no_session(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as broken:
        broken.setattr(SqlTransaction, "get_directory", _locked)
        response = await site.login_as(GROUP_ADMIN)

    assert response.status_code == 503  # fail closed: a page with a text, no session
    assert "set-cookie" not in response.headers or "session" not in response.headers["set-cookie"]
    assert not await _is_signed_in(site)


async def test_a_database_fault_after_login_shows_no_admin_page(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    await site.login_as(GROUP_ADMIN)
    assert await _is_signed_in(site)

    monkeypatch.setattr(SqlTransaction, "get_directory", _locked)

    response = await site.client.get("/admin/")  # the rights cannot be read: no default role

    assert response.status_code == 503
    assert "Admin" not in response.text  # the error page, not the portal

"""The machine API over HTTP (ADR 0023): token endpoint, routes, errors, docs, switches."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import AuditEventRow
from app.api.agent.auth import RequireScope
from app.config import Settings
from app.domain.api_clients import IssuedSecret
from app.domain.audit import Outcome
from app.domain.services import BookingService
from tests.support.agent_api import BASE, FORM, OPEN_ROUTES, SUPER, TRANSFER, Agent, agent_api
from tests.support.configuration_package import PASSPHRASE
from tests.support.legacy_package import package_of
from tests.support.routes import all_routes


async def test_without_the_environment_switch_the_api_does_not_exist(
    settings: Settings, database_url: str
) -> None:
    off = settings.model_copy(update={"database_url": database_url})
    async for agent in agent_api(off, enabled=True):
        response = await agent.client.post(f"{BASE}/oauth/token", content="", headers=FORM)
        assert response.status_code == 404


async def test_client_credentials_flow_end_to_end(agent: Agent) -> None:
    issued = await agent.register()

    response = await agent.token(issued)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["scope"] == "availability:read"
    assert body["access_token"].startswith("opa_")

    headers = {"authorization": f"Bearer {body['access_token']}"}
    me = await agent.client.get(f"{BASE}/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["client_id"] == issued.client.client_id
    assert me.json()["groups"] is None
    assert me.headers["cache-control"] == "no-store"

    groups = await agent.client.get(f"{BASE}/groups", headers=headers)
    assert [g["slug"] for g in groups.json()] == ["demo"]

    slots = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "2026-01-05", "to": "2026-01-06", "lang": "en"},
        headers=headers,
    )
    assert slots.status_code == 200
    payload = slots.json()
    assert payload["timezone"] == "Europe/Amsterdam"
    assert payload["slots"], "the demo group is open on weekdays"
    first = payload["slots"][0]
    assert set(first) == {"start", "end", "local_date", "local_time", "label", "remaining"}
    assert "Monday" in first["label"]


async def test_form_credentials_work_too_but_not_both(agent: Agent) -> None:
    issued = await agent.register()
    form = {
        "grant_type": "client_credentials",
        "client_id": issued.client.client_id,
        "client_secret": issued.secret,
    }

    ok = await agent.client.post(
        f"{BASE}/oauth/token", content=str(httpx.QueryParams(form)), headers=FORM
    )
    both = await agent.token(issued, client_id=issued.client.client_id)

    assert ok.status_code == 200
    assert (both.status_code, both.json()) == (400, {"error": "invalid_request"})


@pytest.mark.parametrize(
    ("content", "headers", "status", "error"),
    [
        ("grant_type=password", FORM, 400, "unsupported_grant_type"),
        ("", FORM, 400, "invalid_request"),
        (
            "grant_type=client_credentials&grant_type=client_credentials",
            FORM,
            400,
            "invalid_request",
        ),
        (
            '{"grant_type": "client_credentials"}',
            {"content-type": "application/json"},
            400,
            "invalid_request",
        ),
        (
            "grant_type=client_credentials",
            {**FORM, "authorization": "Basic !!!"},
            401,
            "invalid_client",
        ),
        (
            "grant_type=client_credentials",
            {**FORM, "authorization": "Bearer x"},
            401,
            "invalid_client",
        ),
        ("grant_type=client_credentials&" + "x" * 5000, FORM, 400, "invalid_request"),
    ],
)
async def test_malformed_token_requests(
    agent: Agent, content: str, headers: dict[str, str], status: int, error: str
) -> None:
    response = await agent.client.post(f"{BASE}/oauth/token", content=content, headers=headers)

    assert (response.status_code, response.json()) == (status, {"error": error})


async def test_wrong_secret_is_invalid_client_and_audited_without_the_secret(
    agent: Agent,
) -> None:
    issued = await agent.register()
    wrong = IssuedSecret(issued.client, "ops_wrong-guess")

    response = await agent.token(wrong)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_client"}
    assert response.headers["www-authenticate"].startswith("Basic")
    log = await agent.app.state.admin_service.audit_log(SUPER)
    event = log[0].event
    assert (event.action, event.outcome, event.reason) == (
        "api.token",
        Outcome.DENIED,
        "bad_secret",
    )
    assert "ops_wrong-guess" not in repr(log)


async def test_unknown_scope_is_invalid_scope(agent: Agent) -> None:
    issued = await agent.register()

    response = await agent.token(issued, scope="bookings:everything")

    assert (response.status_code, response.json()) == (400, {"error": "invalid_scope"})


@pytest.mark.parametrize(
    "authorization", [None, "Bearer", "Bearer opa_guessed", "Basic abc", "Bearer " + "a" * 300]
)
async def test_calls_without_a_valid_token_are_refused(
    agent: Agent, authorization: str | None
) -> None:
    headers = {"authorization": authorization} if authorization else {}

    response = await agent.client.get(f"{BASE}/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_token"}
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_another_organisations_group_is_insufficient_scope(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register())

    response = await agent.client.get(
        f"{BASE}/groups/andere-team/availability",
        params={"from": "2026-01-05", "to": "2026-01-06"},
        headers=headers,
    )
    missing = await agent.client.get(
        f"{BASE}/groups/nope/availability",
        params={"from": "2026-01-05", "to": "2026-01-06"},
        headers=headers,
    )

    assert (response.status_code, response.json()) == (403, {"error": "insufficient_scope"})
    assert (missing.status_code, missing.json()) == (404, {"error": "group_not_found"})


async def test_invalid_input_is_refused_without_echoing_it(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register())

    bad = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "ada@example.org", "to": "2026-01-06"},
        headers=headers,
    )
    too_long = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "2026-01-01", "to": "2026-12-31"},
        headers=headers,
    )

    assert (bad.status_code, bad.json()) == (422, {"error": "invalid_request"})
    assert "ada@example.org" not in bad.text
    assert (too_long.status_code, too_long.json()) == (422, {"error": "invalid_range"})


async def test_the_emergency_stop_refuses_tokens_and_calls(agent: Agent) -> None:
    issued = await agent.register()
    headers = await agent.bearer(issued)

    await agent.service.save_settings(SUPER, enabled=False, docs_public=False)

    call = await agent.client.get(f"{BASE}/me", headers=headers)
    token = await agent.token(issued)
    assert (call.status_code, call.json()) == (503, {"error": "temporarily_unavailable"})
    assert token.status_code == 503
    assert call.headers["retry-after"] == "300"


async def test_disabling_a_client_stops_its_token_at_once(agent: Agent) -> None:
    issued = await agent.register()
    headers = await agent.bearer(issued)

    await agent.service.set_enabled(SUPER, issued.client.client_id, False)

    assert (await agent.client.get(f"{BASE}/me", headers=headers)).status_code == 401


async def test_docs_exist_only_while_published(agent: Agent) -> None:
    hidden_spec = await agent.client.get(f"{BASE}/openapi.json")
    hidden_page = await agent.client.get(f"{BASE}/docs")
    assert (hidden_spec.status_code, hidden_page.status_code) == (404, 404)
    assert hidden_spec.json() == {"error": "not_found"}

    await agent.service.save_settings(SUPER, enabled=True, docs_public=True)

    spec = await agent.client.get(f"{BASE}/openapi.json")
    page = await agent.client.get(f"{BASE}/docs", params={"lang": "nl"})
    assert spec.status_code == 200
    paths = set(spec.json()["paths"])
    assert paths == {
        "/oauth/token",
        "/me",
        "/groups",
        "/groups/{slug}/availability",
        "/groups/{slug}/change-requests",
        "/change-requests/{request_id}",
        "/reports/usage",
        "/configuration",
        "/configuration/plan",
        "/configuration/apply",
        "/configuration/package",
    }
    assert spec.json()["servers"] == [{"url": BASE}]
    schemes = spec.json()["components"]["securitySchemes"]
    assert "clientCredentials" in schemes["clientCredentials"]["flows"]
    assert page.status_code == 200
    assert "/groups/{slug}/availability" in page.text
    assert "script" not in page.headers["content-security-policy"]


async def test_unknown_paths_answer_json(agent: Agent) -> None:
    response = await agent.client.get(f"{BASE}/bookings")

    assert (response.status_code, response.json()) == (404, {"error": "not_found"})


async def test_tokens_are_rate_limited_per_ip(settings: Settings, database_url: str) -> None:
    limited = settings.model_copy(
        update={
            "database_url": database_url,
            "agent_api_enabled": True,
            "rate_limit_enabled": True,
            "rate_limit_agent_tokens": "2/minute",
        }
    )
    async for agent in agent_api(limited, enabled=True):
        wrong = IssuedSecret((await agent.register()).client, "ops_guess")
        statuses = [(await agent.token(wrong)).status_code for _ in range(3)]
        assert statuses == [401, 401, 429]


def test_every_route_declares_one_scope_except_token_and_docs(agent: Agent) -> None:
    mounted = next(r for r in agent.app.routes if getattr(r, "path", None) == BASE)
    routes = all_routes(mounted.app.routes)  # type: ignore[attr-defined]

    for route in routes:
        found = [d.call for d in route.dependant.dependencies if isinstance(d.call, RequireScope)]
        expected = 0 if route.path in OPEN_ROUTES else 1
        assert len(found) == expected, route.path
    assert OPEN_ROUTES <= {r.path for r in routes}


async def test_booking_service_is_shared(agent: Agent) -> None:
    # The API reads availability through the participants' service: same rules, same window.
    mounted = next(r for r in agent.app.routes if getattr(r, "path", None) == BASE)
    assert isinstance(mounted.app.state.booking_service, BookingService)  # type: ignore[attr-defined]
    assert mounted.app.state.booking_service is agent.app.state.booking_service  # type: ignore[attr-defined]


# --- configuration document (ADR 0099, ADR 0089 point 7) ----------------------------------

DOCUMENT = "application/vnd.openplanner.config+json"


async def _audit(agent: Agent) -> list[AuditEventRow]:
    async with AsyncSession(agent.app.state.engine) as session:
        return list(await session.scalars(select(AuditEventRow).order_by(AuditEventRow.id)))


async def _document(agent: Agent, headers: dict[str, str]) -> dict[str, object]:
    exported = await agent.client.get(f"{BASE}/configuration", headers=headers)
    assert exported.status_code == 200, exported.text
    return exported.json()  # type: ignore[no-any-return]


def _renamed(document: dict[str, object], name: str) -> dict[str, object]:
    organisations = document["organisations"]
    assert isinstance(organisations, list)
    organisations[0]["name"] = name
    return document


async def test_the_configuration_routes_need_the_transfer_scope(agent: Agent) -> None:
    reader = await agent.bearer(await agent.register())

    answers = [
        await agent.client.get(f"{BASE}/configuration", headers=reader),
        await agent.client.post(f"{BASE}/configuration/plan", headers=reader, content=b"{}"),
        await agent.client.post(f"{BASE}/configuration/apply", headers=reader, content=b"{}"),
        await agent.client.put(f"{BASE}/configuration", headers=reader, content=b"{}"),
    ]

    for response in answers:
        assert (response.status_code, response.json()) == (403, {"error": "insufficient_scope"})
        assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    events = [e for e in await _audit(agent) if e.action == "configuration:transfer"]
    assert [(e.outcome, e.reason) for e in events] == [(Outcome.DENIED, "not_permitted")] * 4


async def test_the_configuration_is_exported_as_a_document(agent: Agent) -> None:
    # A client of another organisation, narrowed to one of its groups, still acts on the
    # whole installation: the scope is global (ADR 0089 point 7).
    issued = await agent.register(organisation="andere", groups=["andere-team"], **TRANSFER)
    headers = await agent.bearer(issued)

    exported = await agent.client.get(f"{BASE}/configuration", headers=headers)

    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == DOCUMENT
    assert exported.headers["content-disposition"].startswith(
        'attachment; filename="openplanner-configuration-'
    )
    assert exported.headers["content-disposition"].endswith('.json"')
    assert exported.headers["cache-control"] == "no-store"
    document = exported.json()
    assert document["format"] == "openplanner.config/v1"
    assert sorted(o["slug"] for o in document["organisations"]) == ["andere", "demo"]
    assert "seal" not in document, "without a passphrase the document holds no secret"


async def test_an_exported_document_plans_to_nothing_and_a_change_is_applied(agent: Agent) -> None:
    issued = await agent.register(**TRANSFER)
    headers = await agent.bearer(issued)
    document = await _document(agent, headers)

    same = await agent.client.post(f"{BASE}/configuration/plan", headers=headers, json=document)
    assert same.status_code == 200, same.text
    assert same.json()["ok"] is True
    assert same.json()["changes"] == []

    changed = _renamed(document, "Hernoemd")
    plan = await agent.client.post(f"{BASE}/configuration/plan", headers=headers, json=changed)
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert set(body) == {"ok", "plan_hash", "changes", "refusals"}
    assert [(c["action"], c["kind"]) for c in body["changes"]] == [("update", "organisation")]
    assert "Hernoemd" not in plan.text, "a plan names fields, never values"

    applied = await agent.client.post(
        f"{BASE}/configuration/apply",
        headers={**headers, "x-plan-hash": body["plan_hash"]},
        json=changed,
    )

    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["applied"] is True
    assert result["settings_version"] >= 1
    assert result["plan"]["changes"] == body["changes"]
    exported_again = await _document(agent, headers)
    assert exported_again["organisations"][0]["name"] == "Hernoemd"  # type: ignore[index]
    events = [e for e in await _audit(agent) if e.detail.startswith("configuration.apply")]
    assert events, "every applied change is an audit event"
    assert {e.actor for e in events} == {issued.client.client_id}
    assert all("Hernoemd" not in e.detail + e.reason for e in events)


async def test_an_apply_is_refused_when_the_plan_is_not_the_reviewed_one(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))
    changed = _renamed(await _document(agent, headers), "Anders")

    stale = await agent.client.post(
        f"{BASE}/configuration/apply", headers={**headers, "x-plan-hash": "0" * 64}, json=changed
    )

    assert (stale.status_code, stale.json()) == (409, {"error": "configuration_plan_stale"})
    assert (await _document(agent, headers))["organisations"][0]["name"] != "Anders"  # type: ignore[index]


async def test_a_plan_that_deletes_needs_the_reviewed_hash(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))
    document = await _document(agent, headers)
    organisations = document["organisations"]
    assert isinstance(organisations, list)
    organisations[0]["groups"] = []
    document["mode"] = "replace"

    plan = await agent.client.post(f"{BASE}/configuration/plan", headers=headers, json=document)
    without = await agent.client.post(f"{BASE}/configuration/apply", headers=headers, json=document)
    reviewed = await agent.client.post(
        f"{BASE}/configuration/apply",
        headers={**headers, "x-plan-hash": plan.json()["plan_hash"]},
        json=document,
    )

    assert [(c["action"], c["kind"]) for c in plan.json()["changes"]] == [("delete", "group")]
    assert plan.json()["changes"][0]["fields"] == []
    assert (without.status_code, without.json()) == (409, {"error": "configuration_plan_stale"})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["applied"] is True


async def test_a_refused_plan_is_a_422_with_its_refusals_and_writes_nothing(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))
    document = await _document(agent, headers)
    organisations = document["organisations"]
    assert isinstance(organisations, list)
    organisations[0]["groups"][0]["slot_minutes"] = 7  # a length the portal refuses

    plan = await agent.client.post(f"{BASE}/configuration/plan", headers=headers, json=document)
    applied = await agent.client.post(f"{BASE}/configuration/apply", headers=headers, json=document)

    assert plan.status_code == 200
    assert plan.json()["ok"] is False
    assert plan.json()["refusals"] == [
        {
            "path": "organisations[demo].groups[demo]",
            "code": "invalid_configuration",
            "field": "slot_minutes",
        }
    ]
    assert applied.status_code == 422
    assert applied.json()["applied"] is False
    assert applied.json()["settings_version"] is None
    assert applied.json()["plan"]["refusals"] == plan.json()["refusals"]


async def test_a_sealed_document_needs_its_passphrase(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))

    exported = await agent.client.get(
        f"{BASE}/configuration", headers={**headers, "x-passphrase": PASSPHRASE}
    )

    assert exported.status_code == 200, exported.text
    assert PASSPHRASE not in exported.text
    plan = await agent.client.post(
        f"{BASE}/configuration/plan",
        headers={**headers, "x-passphrase": PASSPHRASE},
        content=exported.content,
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["changes"] == []


async def test_a_plain_export_is_refused_until_the_host_allows_it(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))

    refused = await agent.client.get(
        f"{BASE}/configuration", headers={**headers, "x-plain-secrets": "true"}
    )

    assert (refused.status_code, refused.json()) == (
        403,
        {"error": "configuration_plaintext_disabled"},
    )
    assert [e for e in await _audit(agent) if e.detail.endswith("plain")] == []


async def test_a_plain_export_holds_clear_text_without_a_passphrase_and_applies_again(
    agent: Agent,
) -> None:
    agent.app.state.settings = agent.app.state.settings.model_copy(
        update={"configuration_plaintext_export_allowed": True}
    )
    headers = await agent.bearer(await agent.register(**TRANSFER))

    exported = await agent.client.get(
        f"{BASE}/configuration", headers={**headers, "x-plain-secrets": "true"}
    )

    assert exported.status_code == 200, exported.text
    assert exported.headers["cache-control"] == "no-store"
    assert '"sealed"' not in exported.text
    assert "seal" not in exported.json()
    plan = await agent.client.post(
        f"{BASE}/configuration/plan", headers=headers, content=exported.content
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["changes"] == []
    events = [e for e in await _audit(agent) if e.detail.endswith("secrets,plain")]
    assert len(events) == 1


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({"x-passphrase": "tooshort9"}, (422, "configuration_passphrase_short"))],
)
async def test_an_export_with_a_short_passphrase_is_refused_by_code(
    agent: Agent, headers: dict[str, str], expected: tuple[int, str]
) -> None:
    token = await agent.bearer(await agent.register(**TRANSFER))

    response = await agent.client.get(f"{BASE}/configuration", headers={**token, **headers})

    assert (response.status_code, response.json()["error"]) == expected
    assert "tooshort9" not in response.text


@pytest.mark.parametrize("route", ["plan", "apply"])
async def test_a_document_is_refused_by_code_never_echoing_the_file(
    agent: Agent, route: str
) -> None:
    token = await agent.bearer(await agent.register(**TRANSFER))
    limit = agent.app.state.settings.configuration_package_max_mb * 1024 * 1024
    url = f"{BASE}/configuration/{route}"

    rubbish = await agent.client.post(url, headers=token, content=b"nope")
    empty = await agent.client.post(url, headers=token, content=b"")
    foreign = await agent.client.post(
        url,
        headers=token,
        json={"format": "openplanner.config/v1", "scope": {"organisation": "demo"}},
    )
    huge = await agent.client.post(url, headers=token, content=b"\0" * (limit + 1))
    declared = await agent.client.post(
        url, headers={**token, "content-length": str(limit + 1)}, content=b""
    )

    for refused in (rubbish, empty):
        assert refused.status_code == 422
        assert "nope" not in refused.text
    assert (foreign.status_code, foreign.json()) == (422, {"error": "configuration_out_of_scope"})
    assert (huge.status_code, huge.json()) == (413, {"error": "configuration_package_too_large"})
    assert (declared.status_code, declared.json()) == (
        413,
        {"error": "configuration_package_too_large"},
    )


def _legacy_package(agent: Agent, tmp_path: Path) -> bytes:
    """A SQLite package of the installation, as ADR 0089 exported it."""
    settings = agent.app.state.settings
    return package_of(settings, agent.app.state.clock.now(), PASSPHRASE, tmp_path)


async def test_a_sqlite_package_is_still_read_as_a_replacing_document(
    agent: Agent, tmp_path: Path
) -> None:
    issued = await agent.register(**TRANSFER)
    headers = await agent.bearer(issued)
    package = _legacy_package(agent, tmp_path)

    imported = await agent.client.put(
        f"{BASE}/configuration/package",
        headers={**headers, "x-passphrase": PASSPHRASE},
        content=package,
    )

    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert set(body) == {
        "installation",
        "app_version",
        "created_at",
        "revision",
        "counts",
        "secrets",
    }
    assert body["counts"]["organisations"] == 2
    rows = await _audit(agent)
    details = [e.detail for e in rows if e.detail.startswith("configuration.import")]
    assert details
    assert details[-1].startswith("configuration.import,organisations:2,")
    assert all(PASSPHRASE not in e.detail + e.reason for e in rows)


async def test_a_sqlite_package_is_refused_by_code(agent: Agent, tmp_path: Path) -> None:
    token = await agent.bearer(await agent.register(**TRANSFER))
    package = _legacy_package(agent, tmp_path)
    url = f"{BASE}/configuration/package"

    wrong = await agent.client.put(
        url, headers={**token, "x-passphrase": "not the passphrase"}, content=package
    )
    missing = await agent.client.put(url, headers=token, content=package)

    assert (wrong.status_code, wrong.json()) == (422, {"error": "configuration_passphrase_wrong"})
    assert (missing.status_code, missing.json()) == (
        422,
        {"error": "configuration_passphrase_short"},
    )


async def test_put_applies_a_document_exactly_as_apply_does(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))
    changed = _renamed(await _document(agent, headers), "Via PUT")

    response = await agent.client.put(f"{BASE}/configuration", headers=headers, json=changed)

    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    assert (await _document(agent, headers))["organisations"][0]["name"] == "Via PUT"  # type: ignore[index]


async def test_put_of_a_deleting_document_needs_the_reviewed_hash_like_apply(
    agent: Agent,
) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))
    document = await _document(agent, headers)
    document["mode"] = "replace"
    document["organisations"] = document["organisations"][:1]  # type: ignore[index]

    without = await agent.client.put(f"{BASE}/configuration", headers=headers, json=document)

    assert without.status_code in (409, 422)
    assert without.status_code != 200


async def test_the_package_route_does_not_take_a_json_document(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**TRANSFER))

    refused = await agent.client.put(
        f"{BASE}/configuration/package",
        headers={**headers, "x-passphrase": PASSPHRASE},
        json=await _document(agent, headers),
    )

    assert refused.status_code == 422
    assert refused.json() == {"error": "configuration_package_invalid"}

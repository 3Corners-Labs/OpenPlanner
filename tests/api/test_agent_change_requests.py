"""Change requests over the machine API (ADR 0023 step 2): scope, answers, status, refusals."""

from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import AuditEventRow, ChangeRequestRow, JobRow
from app.config import Settings
from app.domain.change_request_service import ChangeRequestService
from app.domain.services import BookingService
from tests.support.agent_api import BASE, Agent, agent_api
from tests.support.domain import ADA, book_confirmed

ASK = {"scopes": ["bookings:request_change", "availability:read"]}


@pytest.fixture
async def agent(settings: Settings, database_url: str) -> AsyncIterator[Agent]:
    """The app with the machine API on and change requests switched on for the organisation."""
    on = settings.model_copy(
        update={
            "database_url": database_url,
            "agent_api_enabled": True,
            "agent_change_requests_enabled": True,
        }
    )
    async for running in agent_api(on, enabled=True):
        yield running


async def _free(agent: Agent, headers: dict[str, str]) -> list[datetime]:
    response = await agent.client.get(
        f"{BASE}/groups/demo/availability",
        params={"from": "2026-01-05", "to": "2026-01-06"},
        headers=headers,
    )
    return [datetime.fromisoformat(s["start"]) for s in response.json()["slots"]]


async def _booked(agent: Agent, start: datetime) -> tuple[int, str]:
    """Book ``start`` in demo for Ada (phone 06 1234 5678); return its id and its code."""
    bookings: BookingService = agent.app.state.booking_service
    changes: ChangeRequestService = agent.app.state.change_request_service
    receipt = await book_confirmed(
        bookings, group_slug="demo", start=start, contact=ADA, locale="nl"
    )
    details = await bookings.find(receipt.management_token)
    code = changes.code(receipt.booking, details.group.organisation_id)
    assert code is not None
    return receipt.booking.id or 0, code


async def test_a_change_request_answers_the_same_with_or_without_a_match(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register(**ASK))
    first, second = (await _free(agent, headers))[:2]
    booking_id, code = await _booked(agent, first)
    url = f"{BASE}/groups/demo/change-requests"

    matched = await agent.client.post(
        url, json={"action": "cancel", "phone": "06 1234 5678", "code": code}, headers=headers
    )
    unmatched = await agent.client.post(
        url,
        json={"action": "cancel", "phone": "06 1234 5678", "code": "0000 0000"},
        headers=headers,
    )
    moved = await agent.client.post(
        url,
        json={
            "action": "reschedule",
            "phone": "+31612345678",
            "code": code,
            "start": second.isoformat(),
        },
        headers=headers,
    )

    for response in (matched, unmatched, moved):
        assert response.status_code == 202, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert set(body) == {"id", "action", "start", "status", "expires_at"}
        assert body["status"] == "pending"
        assert body["id"].startswith("chr_")
    assert matched.json()["expires_at"] == unmatched.json()["expires_at"]
    async with AsyncSession(agent.app.state.engine) as session:
        rows = (await session.scalars(select(ChangeRequestRow).order_by(ChangeRequestRow.id))).all()
        jobs = (
            await session.scalars(select(JobRow.type).where(JobRow.booking_id == booking_id))
        ).all()
        events = (
            await session.scalars(
                select(AuditEventRow).where(AuditEventRow.action == "bookings:request_change")
            )
        ).all()
    # The reschedule came within the cooldown (step 2b): recorded as no match, nothing sent.
    assert [r.booking_id for r in rows] == [booking_id, None, None]
    assert jobs.count("send_change_request") == 2  # the first request's email and SMS
    assert [e.reason for e in events] == ["matched", "no_match", "cooldown"]
    assert all("612345678" not in f"{e.detail}{e.reason}" for e in events)

    status = await agent.client.get(
        f"{BASE}/change-requests/{matched.json()['id']}", headers=headers
    )
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert status.json()["expires_at"] == matched.json()["expires_at"]


async def test_the_status_belongs_to_the_asking_client(agent: Agent) -> None:
    mine = await agent.bearer(await agent.register(**ASK))
    theirs = await agent.bearer(await agent.register(name="Other bot", **ASK))
    asked = await agent.client.post(
        f"{BASE}/groups/demo/change-requests",
        json={"action": "cancel", "phone": "06 1234 5678", "code": "12345678"},
        headers=mine,
    )

    own = await agent.client.get(f"{BASE}/change-requests/{asked.json()['id']}", headers=mine)
    other = await agent.client.get(f"{BASE}/change-requests/{asked.json()['id']}", headers=theirs)
    malformed = await agent.client.get(f"{BASE}/change-requests/not-an-id", headers=mine)

    assert own.status_code == 200
    assert own.json()["status"] == "pending"
    assert other.status_code == 404
    assert other.json() == {"error": "change_request_not_found"}
    assert malformed.status_code == 422


async def test_the_scope_is_needed(agent: Agent) -> None:
    headers = await agent.bearer(await agent.register())  # availability:read only

    response = await agent.client.post(
        f"{BASE}/groups/demo/change-requests",
        json={"action": "cancel", "phone": "06 1234 5678", "code": "12345678"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json() == {"error": "insufficient_scope"}


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"action": "cancel", "phone": "06 1234 5678", "code": "1234"}, 422, "invalid_request"),
        (
            {"action": "cancel", "phone": "06 1234 5678", "code": "1234567x"},
            422,
            "invalid_appointment_code",
        ),
        ({"action": "cancel", "phone": "123456", "code": "12345678"}, 422, "invalid_contact"),
        (
            {"action": "reschedule", "phone": "06 1234 5678", "code": "12345678"},
            400,
            "invalid_request",
        ),
        (
            {
                "action": "cancel",
                "phone": "06 1234 5678",
                "code": "12345678",
                "start": "2026-01-05T08:00:00Z",
            },
            400,
            "invalid_request",
        ),
        (
            {
                "action": "reschedule",
                "phone": "06 1234 5678",
                "code": "12345678",
                "start": "2026-01-05T08:07:00Z",
            },
            409,
            "slot_unavailable",
        ),
    ],
)
async def test_refusals_say_nothing_about_a_booking(
    agent: Agent, body: dict[str, str], status: int, error: str
) -> None:
    headers = await agent.bearer(await agent.register(**ASK))

    response = await agent.client.post(
        f"{BASE}/groups/demo/change-requests", json=body, headers=headers
    )

    assert response.status_code == status, response.text
    assert response.json() == {"error": error}


async def test_an_organisation_without_change_requests_refuses_them(
    settings: Settings, database_url: str
) -> None:
    on = settings.model_copy(update={"database_url": database_url, "agent_api_enabled": True})
    async for running in agent_api(on, enabled=True):
        headers = await running.bearer(await running.register(**ASK))

        response = await running.client.post(
            f"{BASE}/groups/demo/change-requests",
            json={"action": "cancel", "phone": "06 1234 5678", "code": "12345678"},
            headers=headers,
        )

        assert response.status_code == 422
        assert response.json() == {"error": "change_requests_disabled"}

"""A planner's changes from the agenda in the admin portal (ADR 0044 step 2)."""

import itertools
from datetime import UTC, datetime, time, timedelta

import httpx

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.admin import AdminService
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.planning import PlanningService
from app.domain.scheduling import ScheduleOverride, TimeWindow
from app.domain.services import BookingService
from tests.admin.conftest import GROUP_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.admin.test_agenda_routes import FRIDAY
from tests.admin.test_call_routes import FRIDAY_0900
from tests.support.domain import ADA, TEST_TOKEN_KEY, citizen

FRIDAY_0930 = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)
AGENDA = "/admin/groups/demo/agenda"


def _uow(site: Site) -> SqlUnitOfWork:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    return SqlUnitOfWork(app.state.engine)


_CITIZENS = itertools.count(1)
"""Each booking of ``_book`` is another citizen, so the address limit (ADR 0059) stays out."""


async def _book(site: Site, start: datetime = FRIDAY_0900) -> int:
    receipt = await BookingService(_uow(site), site.clock, TEST_TOKEN_KEY).book(
        group_slug="demo", start=start, contact=citizen(next(_CITIZENS)), locale="nl"
    )
    return receipt.booking.id or 0


async def _staff_friday(site: Site, agents: int) -> None:
    planning = PlanningService(_uow(site), site.clock, AdminService(_uow(site), site.clock))
    window = TimeWindow(time(9), time(10), agents)
    await planning.add_override(SUPER, "demo", ScheduleOverride(FRIDAY, window))


async def _status(site: Site, booking_id: int) -> tuple[BookingStatus, ChangeReason | None]:
    async with _uow(site).read() as tx:
        booking = await tx.get_booking(booking_id)
    assert booking is not None
    return booking.status, booking.change_reason


async def _post(site: Site, path: str, data: dict[str, str]) -> httpx.Response:
    return await site.client.post(path, data=data, headers=SAME_ORIGIN)


async def test_the_agenda_offers_move_and_cancel_per_booking(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text

    assert f'href="{AGENDA}/bookings/{booking_id}/move"' in text
    assert f'href="{AGENDA}/bookings/{booking_id}/cancel"' in text


async def test_a_planner_moves_a_booking_to_a_free_slot(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)

    form = (await site.client.get(f"{AGENDA}/bookings/{booking_id}/move")).text
    response = await _post(
        site, f"{AGENDA}/bookings/{booking_id}/move", {"start": FRIDAY_0930.isoformat()}
    )

    assert f"Afspraak OP-{booking_id:06d} verplaatsen" in form
    assert "Ada, vrijdag 2 januari 2026, 09:00" in form
    assert f'value="{FRIDAY_0930.isoformat()}"' in form
    assert response.status_code == 303
    assert response.headers["location"] == f"{AGENDA}?day=2026-01-02&done=moved&count=1"
    assert (await _status(site, booking_id))[0] is BookingStatus.CANCELLED


async def test_a_move_into_a_short_slot_needs_the_planners_confirmation(site: Site) -> None:
    first = await _book(site, FRIDAY_0900)
    await _book(site, FRIDAY_0930)
    await _staff_friday(site, 1)  # 09:30 keeps its snapshot of 2 places, but 1 agent
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{first}/move"

    refused = await _post(site, path, {"start": FRIDAY_0930.isoformat()})
    accepted = await _post(
        site, path, {"start": FRIDAY_0930.isoformat(), "accept_shortfall": "yes"}
    )

    assert refused.status_code == 409
    assert "meer afspraken dan medewerkers" in refused.text
    assert accepted.status_code == 303


async def test_a_move_onto_the_same_address_tells_the_planner_why(site: Site) -> None:
    bookings = BookingService(_uow(site), site.clock, TEST_TOKEN_KEY)
    receipts = [
        await bookings.book(group_slug="demo", start=start, contact=ADA, locale="nl")
        for start in (FRIDAY_0900, FRIDAY_0930)
    ]
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{receipts[0].booking.id}/move"

    refused = await _post(site, path, {"start": FRIDAY_0930.isoformat()})

    assert refused.status_code == 409
    assert "Afspraken per e-mailadres" in refused.text
    assert "Heeft u al een afspraak" not in refused.text


async def test_a_planner_cancels_with_a_fixed_reason(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{booking_id}/cancel"

    form = (await site.client.get(path)).text
    invalid = await _post(site, path, {"reason": "my own words"})
    response = await _post(site, path, {"reason": "technical"})

    assert '<option value="technical">Technische storing</option>' in form
    assert "De burger krijgt hiervan altijd bericht" in form
    assert invalid.status_code == 422
    assert response.status_code == 303
    assert await _status(site, booking_id) == (BookingStatus.CANCELLED, ChangeReason.TECHNICAL)


async def test_a_booking_that_starts_soon_needs_a_phone_call(site: Site) -> None:
    booking_id = await _book(site)
    site.clock.advance(FRIDAY_0900 - timedelta(minutes=30) - site.clock.now())
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"{AGENDA}/bookings/{booking_id}/move")
    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text

    assert response.status_code == 409
    assert "Bel de deelnemer" in response.text
    assert f"/bookings/{booking_id}/move" not in agenda


async def test_a_planner_accepts_a_shortfall_until_the_slot_changes(site: Site) -> None:
    await _book(site)
    await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    before = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    response = await _post(site, f"{AGENDA}/accept", {"start": FRIDAY_0900.isoformat()})
    after = (await site.client.get(response.headers["location"])).text
    overview = (await site.client.get("/admin/groups/demo")).text

    assert "Tekort accepteren" in before
    assert response.status_code == 303
    assert "#slot-20260102T0800" in response.headers["location"]
    assert "Tekort geaccepteerd" in after
    assert "Het tekort is geaccepteerd" in after
    assert "Tijdsloten met meer afspraken dan medewerkers" not in overview


async def test_a_slot_without_a_shortfall_cannot_be_accepted(site: Site) -> None:
    await _book(site)
    await site.login_as(GROUP_ADMIN)

    response = await _post(site, f"{AGENDA}/accept", {"start": FRIDAY_0900.isoformat()})

    assert response.status_code == 409


async def test_the_cancel_form_in_english(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"{AGENDA}/bookings/{booking_id}/cancel?lang=en")).text

    assert f"Cancel booking OP-{booking_id:06d}" in text
    assert '<option value="staffing" selected>Not enough agents</option>' in text


async def test_the_audit_log_names_the_change_and_the_reason_in_words(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)
    await _post(site, f"{AGENDA}/bookings/{booking_id}/cancel", {"reason": "unforeseen"})

    text = (await site.client.get("/admin/audit")).text

    assert "afspraak geannuleerd door een planner, reden: Onvoorziene omstandigheden" in text
    assert "Ada" not in text


async def test_an_accept_in_local_time_lands_on_the_same_slot(site: Site) -> None:
    await _book(site)
    await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    response = await _post(site, f"{AGENDA}/accept", {"start": "2026-01-02T09:00:00+01:00"})

    assert response.status_code == 303
    assert response.headers["location"].endswith("#slot-20260102T0800")


# --- ask to rebook ------------------------------------------------------------------------


async def test_a_planner_asks_a_citizen_to_choose_a_new_time(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{booking_id}/rebook"

    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    form = (await site.client.get(path)).text
    response = await _post(site, path, {"reason": "staffing"})
    after = (await site.client.get(response.headers["location"])).text
    waiting = (await site.client.get(f"{AGENDA}/waiting")).text

    assert f'href="{path}"' in agenda
    assert "De plek in dit tijdslot komt meteen vrij" in form
    assert response.status_code == 303
    assert "Burgers gevraagd een nieuwe tijd te kiezen: 1" in after
    assert "Wacht op nieuwe tijd</span>" in after
    assert f"OP-{booking_id:06d} · Ada" in waiting
    assert "vrijdag 16 januari 2026, 09:00" in waiting  # lapses 14 days after the start
    assert (await _status(site, booking_id)) == (
        BookingStatus.AWAITING_REBOOK,
        ChangeReason.STAFFING,
    )


async def test_a_short_slot_is_asked_to_rebook_after_a_confirmation(site: Site) -> None:
    await _book(site)
    site.clock.advance(timedelta(minutes=1))
    newest = await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    confirm = await site.client.get(
        f"{AGENDA}/slot-rebook", params={"start": FRIDAY_0900.isoformat()}
    )
    response = await _post(
        site,
        f"{AGENDA}/slot-rebook",
        {"start": FRIDAY_0900.isoformat(), "ids": str(newest), "reason": "technical"},
    )

    assert "Burgers om een nieuwe tijd vragen" in agenda
    assert confirm.status_code == 200
    assert f"OP-{newest:06d} · Ada" in confirm.text
    assert "Om een nieuwe tijd vragen (1)" in confirm.text
    assert response.status_code == 303
    assert (await _status(site, newest))[0] is BookingStatus.AWAITING_REBOOK


async def test_a_bulk_request_for_a_changed_slot_shows_the_new_list(site: Site) -> None:
    first = await _book(site)
    site.clock.advance(timedelta(minutes=1))
    await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    response = await _post(
        site,
        f"{AGENDA}/slot-rebook",
        {"start": FRIDAY_0900.isoformat(), "ids": str(first), "reason": "technical"},
    )

    assert response.status_code == 409
    assert "Dit tijdslot is intussen veranderd" in response.text
    assert (await _status(site, first))[0] is BookingStatus.CONFIRMED


async def test_malformed_bulk_ids_are_rejected(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(
        site, f"{AGENDA}/slot-rebook", {"start": FRIDAY_0900.isoformat(), "ids": "1,x"}
    )

    assert response.status_code == 400

"""A planner's changes from the agenda in the admin portal (ADR 0044 step 2)."""

import itertools
import re
from datetime import UTC, datetime, time, timedelta

from app.domain.admin import AdminService
from app.domain.booking import BookingStatus, ChangeReason
from app.domain.planning import PlanningService
from app.domain.scheduling import ScheduleOverride, TimeWindow
from app.domain.services import BookingService
from tests.support.admin_site import (
    GROUP_ADMIN,
    SUPER,
    Site,
    audit_log,
    post_form,
    site_uow,
)
from tests.support.domain import (
    ADA,
    FRIDAY,
    FRIDAY_0900,
    TEST_TOKEN_KEY,
    book_confirmed,
    book_pending,
    participant,
)

FRIDAY_0930 = datetime(2026, 1, 2, 8, 30, tzinfo=UTC)
AGENDA = "/admin/groups/demo/agenda"


_PARTICIPANTS = itertools.count(1)
"""Each booking of ``_book`` is another participant, so the address limit (ADR 0066) stays out."""


async def _book(site: Site, start: datetime = FRIDAY_0900, slug: str = "demo") -> int:
    receipt = await book_confirmed(
        BookingService(site_uow(site), site.clock, TEST_TOKEN_KEY, treatment_buffer=0),
        group_slug=slug,
        start=start,
        contact=participant(next(_PARTICIPANTS)),
        locale="nl",
    )
    return receipt.booking.id or 0


async def _staff_friday(site: Site, agents: int) -> None:
    planning = PlanningService(site_uow(site), site.clock, AdminService(site_uow(site), site.clock))
    window = TimeWindow(time(9), time(10), agents)
    await planning.add_override(SUPER, "demo", ScheduleOverride(FRIDAY, window))


async def _status(site: Site, booking_id: int) -> tuple[BookingStatus, ChangeReason | None]:
    async with site_uow(site).read() as tx:
        booking = await tx.get_booking(booking_id)
    assert booking is not None
    return booking.status, booking.change_reason


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
    response = await post_form(
        site, f"{AGENDA}/bookings/{booking_id}/move", {"start": FRIDAY_0930.isoformat()}
    )

    assert f"Afspraak OP-{booking_id:06d} verplaatsen" in form
    assert "Ada, vrijdag 2 januari 2026, 09:00" in form
    assert f'value="{FRIDAY_0930.isoformat()}"' in form
    assert f'<a href="{AGENDA}?day=2026-01-02">Annuleren</a>' in form  # not the form itself
    assert response.status_code == 303
    assert response.headers["location"] == f"{AGENDA}?day=2026-01-02&done=moved&count=1"
    assert (await _status(site, booking_id))[0] is BookingStatus.CANCELLED


async def test_a_move_into_a_short_slot_needs_the_planners_confirmation(site: Site) -> None:
    first = await _book(site, FRIDAY_0900)
    await _book(site, FRIDAY_0930)
    await _staff_friday(site, 1)  # 09:30 keeps its snapshot of 2 places, but 1 agent
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{first}/move"

    refused = await post_form(site, path, {"start": FRIDAY_0930.isoformat()})
    accepted = await post_form(
        site, path, {"start": FRIDAY_0930.isoformat(), "accept_shortfall": "yes"}
    )

    assert refused.status_code == 409
    assert "meer afspraken dan medewerkers" in refused.text
    assert accepted.status_code == 303


async def test_a_move_onto_the_same_address_tells_the_planner_why(site: Site) -> None:
    bookings = BookingService(site_uow(site), site.clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipts = [
        await book_confirmed(bookings, group_slug="demo", start=start, contact=ADA, locale="nl")
        for start in (FRIDAY_0900, FRIDAY_0930)
    ]
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{receipts[0].booking.id}/move"

    refused = await post_form(site, path, {"start": FRIDAY_0930.isoformat()})

    assert refused.status_code == 409
    assert "Afspraken per e-mailadres" in refused.text
    assert "Heeft u al een afspraak" not in refused.text


async def test_a_planner_cancels_with_a_fixed_reason(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{booking_id}/cancel"

    form = (await site.client.get(path)).text
    invalid = await post_form(site, path, {"reason": "my own words"})
    response = await post_form(site, path, {"reason": "technical"})

    assert '<option value="technical">Technische storing</option>' in form
    assert "De deelnemer krijgt hiervan altijd bericht" in form
    assert invalid.status_code == 422
    assert response.status_code == 303
    assert response.headers["location"] == f"{AGENDA}?day=2026-01-02&done=cancelled&count=1"
    assert await _status(site, booking_id) == (BookingStatus.CANCELLED, ChangeReason.TECHNICAL)


async def test_a_booking_that_starts_soon_needs_a_call_with_the_details_on_request(
    site: Site,
) -> None:
    booking_id = await _book(site)
    site.clock.advance(FRIDAY_0900 - timedelta(minutes=30) - site.clock.now())
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"{AGENDA}/bookings/{booking_id}/move")
    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text

    assert response.status_code == 409
    assert "Neem zelf contact op met de deelnemer" in response.text
    assert "Details en dan Contactgegevens tonen" in response.text  # where the number is
    assert f"/bookings/{booking_id}/move" not in agenda


async def test_a_planner_accepts_a_shortfall_until_the_slot_changes(site: Site) -> None:
    await _book(site)
    await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    before = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    response = await post_form(site, f"{AGENDA}/accept", {"start": FRIDAY_0900.isoformat()})
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

    response = await post_form(site, f"{AGENDA}/accept", {"start": FRIDAY_0900.isoformat()})

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
    await post_form(site, f"{AGENDA}/bookings/{booking_id}/cancel", {"reason": "unforeseen"})

    text = (await site.client.get("/admin/audit")).text

    assert "afspraak geannuleerd door een planner, reden: Onvoorziene omstandigheden" in text
    assert "Ada" not in text


async def test_an_accept_in_local_time_lands_on_the_same_slot(site: Site) -> None:
    await _book(site)
    await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    response = await post_form(site, f"{AGENDA}/accept", {"start": "2026-01-02T09:00:00+01:00"})

    assert response.status_code == 303
    assert response.headers["location"].endswith("#slot-20260102T0800")


# --- ask to rebook ------------------------------------------------------------------------


async def test_a_planner_asks_a_participant_to_choose_a_new_time(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{booking_id}/rebook"

    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    form = (await site.client.get(path)).text
    response = await post_form(site, path, {"reason": "staffing"})
    after = (await site.client.get(response.headers["location"])).text
    waiting = (await site.client.get(f"{AGENDA}/waiting")).text

    assert f'href="{path}"' in agenda
    assert "De plek in dit tijdslot komt meteen vrij" in form
    assert response.status_code == 303
    assert "Deelnemers gevraagd een nieuwe tijd te kiezen: 1" in after
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
    response = await post_form(
        site,
        f"{AGENDA}/slot-rebook",
        {"start": FRIDAY_0900.isoformat(), "ids": str(newest), "reason": "technical"},
    )

    assert "Deelnemers om een nieuwe tijd vragen" in agenda
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

    response = await post_form(
        site,
        f"{AGENDA}/slot-rebook",
        {"start": FRIDAY_0900.isoformat(), "ids": str(first), "reason": "technical"},
    )

    assert response.status_code == 409
    assert "Dit tijdslot is intussen veranderd" in response.text
    assert (await _status(site, first))[0] is BookingStatus.CONFIRMED


async def test_malformed_bulk_ids_are_rejected(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await post_form(
        site, f"{AGENDA}/slot-rebook", {"start": FRIDAY_0900.isoformat(), "ids": "1,x"}
    )

    assert response.status_code == 400


async def _book_pending(site: Site) -> int:
    bookings = BookingService(site_uow(site), site.clock, TEST_TOKEN_KEY, treatment_buffer=0)
    receipt = await book_pending(
        bookings, FRIDAY_0900, "demo", contact=participant(next(_PARTICIPANTS))
    )
    return receipt.booking.id or 0


async def test_a_pending_booking_shows_as_not_confirmed_and_can_be_confirmed(site: Site) -> None:
    """ADR 0056 point 11: text and icon, a filter, and confirming for the participant."""
    pending = await _book_pending(site)
    confirmed = await _book(site)
    await site.login_as(GROUP_ADMIN)

    day = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    only = (await site.client.get(f"{AGENDA}?day=2026-01-02&status=pending")).text

    assert "Niet bevestigd, uiterlijk" in day
    assert "Geannuleerd" not in day  # a pending booking is not a cancelled one
    assert "(waarvan 1 niet bevestigd)" in day
    assert f'action="{AGENDA}/bookings/{pending}/confirm"' in day
    assert f'action="{AGENDA}/bookings/{confirmed}/confirm"' not in day
    assert f"/bookings/{pending}" in only
    assert f"/bookings/{confirmed}" not in only

    response = await post_form(site, f"{AGENDA}/bookings/{pending}/confirm", {"day": "2026-01-02"})
    again = await post_form(site, f"{AGENDA}/bookings/{pending}/confirm", {"day": "2026-01-02"})

    assert response.status_code == 303
    assert "done=confirmed_by_staff" in response.headers["location"]
    assert "done=not_pending" in again.headers["location"]
    assert (await _status(site, pending))[0] is BookingStatus.CONFIRMED


async def test_confirming_after_the_deadline_says_it_lapsed(site: Site) -> None:
    pending = await _book_pending(site)
    async with site_uow(site).read() as tx:
        booking = await tx.get_booking(pending)
    assert booking is not None
    assert booking.confirm_by is not None
    site.clock.advance(booking.confirm_by - site.clock.now() + timedelta(minutes=1))
    await site.login_as(GROUP_ADMIN)

    response = await post_form(site, f"{AGENDA}/bookings/{pending}/confirm", {})

    assert "done=confirm_lapsed" in response.headers["location"]


async def test_a_pending_booking_offers_move_and_cancel_but_not_rebook(site: Site) -> None:
    """ADR 0056 point 11: moved or cancelled like a confirmed one; it lapses on its own."""
    pending = await _book_pending(site)
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text

    assert f'href="{AGENDA}/bookings/{pending}/move"' in text
    assert f'href="{AGENDA}/bookings/{pending}/cancel"' in text
    assert f'href="{AGENDA}/bookings/{pending}/rebook"' not in text


async def test_the_rebook_form_is_refused_for_a_pending_booking(site: Site) -> None:
    pending = await _book_pending(site)
    await site.login_as(GROUP_ADMIN)

    response = await site.client.get(f"{AGENDA}/bookings/{pending}/rebook")

    assert response.status_code == 409


async def test_a_rebook_post_for_a_pending_booking_is_refused_as_its_form_is(site: Site) -> None:
    """A stale or forged POST meets the form's own rule: the same 409 page, nothing written."""
    pending = await _book_pending(site)
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{pending}/rebook"

    refused = await site.client.get(path)
    with_reason = await post_form(site, path, {"reason": "staffing"})
    without_reason = await post_form(site, path, {})

    assert refused.status_code == 409
    for response in (with_reason, without_reason):
        assert response.status_code == 409
        assert response.text == refused.text  # the error page, not the form with the refusal
    assert (await _status(site, pending))[0] is BookingStatus.PENDING_CONFIRMATION


async def test_a_pending_booking_is_cancelled_like_a_confirmed_one(site: Site) -> None:
    """ADR 0056 point 11: the cancel form and its POST keep the rule of any change."""
    pending = await _book_pending(site)
    await site.login_as(GROUP_ADMIN)
    path = f"{AGENDA}/bookings/{pending}/cancel"

    form = await site.client.get(path)
    response = await post_form(site, path, {"reason": "unforeseen"})

    assert form.status_code == 200
    assert response.status_code == 303
    assert await _status(site, pending) == (BookingStatus.CANCELLED, ChangeReason.UNFORESEEN)


async def test_a_reason_post_for_another_groups_booking_is_not_found(site: Site) -> None:
    """With a reason the service's group check answers first: an unknown id's 404 (ADR 0014)."""
    other = await _book(site, slug="andere-team")
    await site.login_as(GROUP_ADMIN)
    before = await audit_log(site)

    for action in ("cancel", "rebook"):
        taken = await post_form(site, f"{AGENDA}/bookings/{other}/{action}", {"reason": "staffing"})
        unknown = await post_form(
            site, f"{AGENDA}/bookings/{other + 1000}/{action}", {"reason": "staffing"}
        )
        assert (taken.status_code, unknown.status_code) == (404, 404)
        assert taken.text == unknown.text
    assert await _status(site, other) == (BookingStatus.CONFIRMED, None)
    assert await audit_log(site) == before


# --- the rebooking assistant (ADR 0057 step 3) -------------------------------------------


def _selected(text: str, name: str) -> str:
    """Return the selected value of the select ``name`` on a page."""
    select = re.search(rf'<select[^>]*name="{name}".*?</select>', text, re.S)
    assert select is not None
    chosen = re.search(r'<option value="([^"]+)" selected', select.group(0))
    assert chosen is not None
    return chosen.group(1)


async def _open_proposal(site: Site, booking_id: int) -> datetime | None:
    async with site_uow(site).read() as tx:
        proposal = await tx.get_open_proposal(booking_id)
    return proposal.start if proposal else None


async def test_one_booking_gets_the_assistants_time_proposed(site: Site) -> None:
    booking_id = await _book(site)
    await site.login_as(GROUP_ADMIN)

    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    form = await site.client.get(f"{AGENDA}/bookings/{booking_id}/propose")
    chosen = _selected(form.text, "start")
    response = await post_form(
        site, f"{AGENDA}/bookings/{booking_id}/propose", {"start": chosen, "reason": "staffing"}
    )

    assert f"/agenda/bookings/{booking_id}/propose" in agenda
    assert form.status_code == 200
    assert f'<a href="{AGENDA}?day=2026-01-02">Annuleren</a>' in form.text  # not the form itself
    assert response.status_code == 303
    assert "done=proposed" in response.headers["location"]
    assert (await _status(site, booking_id))[0] is BookingStatus.AWAITING_REBOOK
    assert await _open_proposal(site, booking_id) == datetime.fromisoformat(chosen)


async def test_a_short_slot_is_reviewed_and_sent(site: Site) -> None:
    await _book(site)
    site.clock.advance(timedelta(minutes=1))
    newest = await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    review = await site.client.get(
        f"{AGENDA}/slot-propose", params={"start": FRIDAY_0900.isoformat()}
    )
    chosen = _selected(review.text, f"start_{newest}")
    unticked = await post_form(
        site,
        f"{AGENDA}/slot-propose",
        {"start": FRIDAY_0900.isoformat(), "ids": str(newest), "reason": "staffing"},
    )
    sent = await post_form(
        site,
        f"{AGENDA}/slot-propose",
        {
            "start": FRIDAY_0900.isoformat(),
            "ids": str(newest),
            "reason": "staffing",
            f"include_{newest}": "yes",
            f"start_{newest}": chosen,
        },
    )

    assert review.status_code == 200
    assert f"OP-{newest:06d} · Ada" in review.text
    assert f'<a href="{AGENDA}?day=2026-01-02">Annuleren</a>' in review.text  # not the review
    assert unticked.status_code == 422
    assert "Vink ten minste één afspraak aan." in unticked.text
    assert sent.status_code == 303
    assert "done=proposals_sent" in sent.headers["location"]
    assert await _open_proposal(site, newest) == datetime.fromisoformat(chosen)


async def test_the_waiting_list_withdraws_or_confirms_a_proposal(site: Site) -> None:
    withdrawn = await _book(site)
    confirmed = await _book(site, FRIDAY_0930)
    await site.login_as(GROUP_ADMIN)
    for booking_id in (withdrawn, confirmed):
        form = await site.client.get(f"{AGENDA}/bookings/{booking_id}/propose")
        await post_form(
            site,
            f"{AGENDA}/bookings/{booking_id}/propose",
            {"start": _selected(form.text, "start"), "reason": "staffing"},
        )

    waiting = (await site.client.get(f"{AGENDA}/waiting")).text
    gone = await post_form(site, f"{AGENDA}/bookings/{withdrawn}/proposal/withdraw", {})
    again = await post_form(site, f"{AGENDA}/bookings/{withdrawn}/proposal/withdraw", {})
    moved = await post_form(site, f"{AGENDA}/bookings/{confirmed}/proposal/confirm", {})

    assert "vastgehouden tot" in waiting
    assert f"/agenda/bookings/{withdrawn}/proposal/withdraw" in waiting
    assert "done=proposal_withdrawn" in gone.headers["location"]
    assert "done=proposal_gone" in again.headers["location"]
    assert await _open_proposal(site, withdrawn) is None
    assert (await _status(site, withdrawn))[0] is BookingStatus.AWAITING_REBOOK
    assert "done=proposal_confirmed_by_staff" in moved.headers["location"]
    assert (await _status(site, confirmed))[0] is BookingStatus.CANCELLED  # moved to a new one


async def test_a_short_day_is_reviewed_at_once(site: Site) -> None:
    await _book(site)
    site.clock.advance(timedelta(minutes=1))
    newest = await _book(site)
    await _staff_friday(site, 1)
    await site.login_as(GROUP_ADMIN)

    agenda = (await site.client.get(f"{AGENDA}?day=2026-01-02")).text
    week = (await site.client.get(f"{AGENDA}/week?week=2026-01-02")).text
    review = await site.client.get(f"{AGENDA}/slot-propose", params={"day": "2026-01-02"})
    too_long = await site.client.get(
        f"{AGENDA}/slot-propose", params={"day": "2026-01-02", "days": "8"}
    )

    assert f"{AGENDA}/slot-propose?day=2026-01-02" in agenda
    assert "/agenda/slot-propose?day=2025-12-29&amp;days=7" in week
    assert review.status_code == 200
    assert f"OP-{newest:06d} · Ada" in review.text
    assert 'name="day" value="2026-01-02"' in review.text
    assert too_long.status_code == 400

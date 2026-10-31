"""The simulator on an admin booking page: play platform events without agents (not in prod).

    POST /admin/groups/{slug}/bookings/{booking_id}/simulate   group.edit (fresh)
         event=prepare            stand-in meeting and task ids for a booking without them,
                                  and an open stand-in trial call where the group offers one
         event=<SimulatedEvent>   sign and post that event to OpenPlanner's own webhook

Each played event takes the real webhook path (signature, parsing, deduplication, timeline,
status) through an in-process request, so no tunnel or Webex agent is needed. In production
``SimulationService`` is switched off and the page shows no simulator.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.adapters.simulated_events import PlayOutcome, SimulatedEvent, play
from app.admin.authz import AdminContext, RequirePermission
from app.admin.forms import raise_refusal, read_form, redirect_done
from app.admin.pages import Locale, SameOrigin
from app.domain.authorization import Permission
from app.domain.booking import Booking
from app.domain.errors import DomainError
from app.domain.ports import Clock
from app.domain.simulation import SimulationService
from app.i18n import BoundGettext
from app.web.protection import RejectedRequest

router = APIRouter(prefix="/admin", include_in_schema=False)

Edit = Annotated[
    AdminContext,
    Depends(RequirePermission(Permission.GROUP_EDIT, group_param="slug", fresh=True)),
]

PREPARE = "prepare"

EVENT_LABELS: dict[SimulatedEvent, str] = {
    SimulatedEvent.TASK_CONNECTED: "admin.simulator.event.task_connected",
    SimulatedEvent.HOST_JOINED: "admin.simulator.event.host_joined",
    SimulatedEvent.GUEST_JOINED: "admin.simulator.event.guest_joined",
    SimulatedEvent.PARTICIPANT_LEFT: "admin.simulator.event.participant_left",
    SimulatedEvent.MEETING_STARTED: "admin.simulator.event.meeting_started",
    SimulatedEvent.MEETING_ENDED: "admin.simulator.event.meeting_ended",
    SimulatedEvent.TASK_FAILED: "admin.simulator.event.task_failed",
    SimulatedEvent.TASK_ENDED: "admin.simulator.event.task_ended",
    SimulatedEvent.TRIAL_ENTERED: "admin.simulator.event.trial_entered",
    SimulatedEvent.TRIAL_LEFT: "admin.simulator.event.trial_left",
}
"""Button label per event, in the order a call usually goes."""

OUTCOME_DONE: dict[PlayOutcome, str] = {
    PlayOutcome.DELIVERED: "simulated",
    PlayOutcome.NO_IDS: "simulator_no_ids",
    PlayOutcome.NO_SECRET: "simulator_no_secret",
    PlayOutcome.REFUSED: "simulator_refused",
}
"""Confirmation (``?done=``) per outcome."""


@dataclass(frozen=True)
class EventButton:
    """One simulator button."""

    value: str
    label: str


@dataclass(frozen=True)
class SimulatorView:
    """What the booking page shows of the simulator."""

    action: str
    prepared: bool
    buttons: list[EventButton]


def simulation_service(request: Request) -> SimulationService:
    """Return the application's simulation service."""
    service: SimulationService = request.app.state.simulation_service
    return service


def simulator_view(
    request: Request, slug: str, booking: Booking, can_edit: bool, _: BoundGettext
) -> SimulatorView | None:
    """Return the simulator card for a booking page, or ``None`` when it is not offered."""
    if not simulation_service(request).enabled or not can_edit or booking.id is None:
        return None
    return SimulatorView(
        action=f"/admin/groups/{slug}/bookings/{booking.id}/simulate",
        prepared=booking.meeting is not None and booking.wxcc_task_id is not None,
        buttons=[EventButton(event.value, _(key)) for event, key in EVENT_LABELS.items()],
    )


@router.post("/groups/{slug}/bookings/{booking_id}/simulate")
async def simulate(
    request: Request, locale: Locale, slug: str, booking_id: int, _: SameOrigin, context: Edit
) -> Response:
    """Prepare stand-in ids or play one platform event for the booking."""
    service = simulation_service(request)
    form = await read_form(request, locale)
    name = form.text("event")
    back = f"/admin/groups/{slug}/bookings/{booking_id}"
    try:
        if name == PREPARE:
            await service.prepare(context.principal, slug, booking_id)
            return redirect_done(back, "simulated_prepared", anchor="simulator")
        try:
            event = SimulatedEvent(name)
        except ValueError as exc:
            raise RejectedRequest(400) from exc
        ids = await service.ids(context.principal, slug, booking_id)
        settings = request.app.state.settings
        clock: Clock = request.app.state.clock
        webex, wxcc = settings.webex_webhook_secret, settings.wxcc_webhook_secret
        played = await play(
            request.app,
            event,
            meeting_id=ids.meeting_id,
            task_id=ids.task_id,
            trial_meeting_id=ids.trial_meeting_id,
            webex_secret=webex.get_secret_value() if webex else None,
            wxcc_secret=wxcc.get_secret_value() if wxcc else None,
            now=clock.now(),
        )
        if played.outcome is PlayOutcome.DELIVERED:
            await service.record_event(context.principal, slug, booking_id, event.value)
    except DomainError as error:
        raise_refusal(error)
        raise
    return redirect_done(back, OUTCOME_DONE[played.outcome], anchor="simulator")

"""The booking and planner services, built once from the live settings.

The web process and the worker both need them; each used to write the same lambdas, so a new
parameter added in one process left the other on its default. Both call these (ADR 0005).
"""

from app.adapters.live_settings import LiveSettings
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.planner_service import PlannerService
from app.domain.pool_booking import PoolBookings
from app.domain.ports import Clock, UnitOfWork
from app.domain.request_watch import RequestWatch
from app.domain.request_work import RequestWork
from app.domain.services import BookingService
from app.keys import management_token_key, management_token_key_previous
from app.policies import confirm_policy, proposal_hours, reach


def booking_service(
    uow: UnitOfWork, clock: Clock, settings: Settings, live: LiveSettings
) -> BookingService:
    """Return the booking service with each organisation's live rules."""
    return BookingService(
        uow,
        clock,
        management_token_key(settings),
        phone_region=lambda org: live.for_organisation(org).phone_default_region,
        confirm_policy=lambda org: confirm_policy(live.for_organisation(org)),
        previous_token_key=management_token_key_previous(settings),
        closure_retention=live.closure_retention,
        reach=lambda org: reach(live.for_organisation(org)),
        treatment_buffer=lambda org: live.for_organisation(org).treatment_buffer_minutes,
    )


def planner_service(
    uow: UnitOfWork,
    clock: Clock,
    admin: AdminService,
    bookings: BookingService,
    live: LiveSettings,
) -> PlannerService:
    """Return the planner service with each organisation's live periods."""
    return PlannerService(
        uow,
        clock,
        admin,
        bookings,
        rebook_days=lambda org: live.for_organisation(org).planner_rebook_days,
        reminder_days=lambda org: live.for_organisation(org).planner_rebook_reminder_days,
        proposal_hours=lambda org: proposal_hours(live.for_organisation(org)),
        reach=lambda org: reach(live.for_organisation(org)),
    )


def request_work(clock: Clock, live: LiveSettings) -> RequestWork:
    """Return the request work of the own queue with each organisation's lease and hours."""
    return RequestWork(
        PoolBookings(
            clock, treatment_buffer=lambda org: live.for_organisation(org).treatment_buffer_minutes
        ),
        lease_minutes=lambda org: live.for_organisation(org).assignment_start_lease_minutes,
        unresolved_hours=lambda org: live.for_organisation(org).request_unresolved_hours,
    )


def request_watch(uow: UnitOfWork, clock: Clock, live: LiveSettings) -> RequestWatch:
    """Return the watchdog round of requests with each organisation's hours."""
    return RequestWatch(uow, clock, lambda org: live.for_organisation(org).request_unresolved_hours)

"""The job runner on fake video and contact-centre adapters, for the call job tests (ADR 0018)."""

from app.domain.admin import AdminService
from app.domain.calls import EventKind
from app.domain.integration import IntegrationService
from app.domain.scheduling import ScheduleRule
from app.domain.services import BookingService
from app.i18n import Translator, load_catalogues
from app.jobs.messages import MessageComposer
from app.jobs.runner import JobRunner
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.jobs import FakeQueue
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, webex_routing, window

WIRED = {
    "video_host_email": "host@example.nl",
    "contact_center": webex_routing("openplanner@example.biz", "openplanner-video"),
}
"""The group fields that connect a group to the video provider and the contact centre."""


def group_uow(**group: object) -> InMemoryUnitOfWork:
    """Return a store with group ``aangifte`` (with ``group``), one agent on Monday 09:00-10:00."""
    state = State()
    state.groups[1] = make_group(id=1, slug="aangifte", name="Aangifte", **group)
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    return InMemoryUnitOfWork(state)


class Setup:
    """A store with a fixed clock, fake video and dispatcher, and the integration service."""

    def __init__(self, uow: InMemoryUnitOfWork) -> None:
        self.uow = uow
        self.clock = FixedClock(NOW)
        self.video = FakeVideo()
        self.dispatcher = FakeDispatcher()
        self.integration = IntegrationService(uow, self.clock, AdminService(uow, self.clock))

    def runner(self, queue: FakeQueue) -> JobRunner:
        """Return a job runner that takes its jobs from ``queue`` and calls the fakes."""
        composer = MessageComposer(Translator(load_catalogues()), TEST_TOKEN_KEY, "http://x")
        return JobRunner(
            queue,
            self.uow,
            self.clock,
            None,
            composer,
            video=self.video,
            dispatchers=webex_cc(self.dispatcher),
            integration=self.integration,
        )

    async def booking(self) -> int:
        """Book and confirm Monday 09:00 in ``aangifte``; return the booking's id."""
        receipt = await book(BookingService(self.uow, self.clock, TEST_TOKEN_KEY))
        return receipt.booking.id or 0

    def events(self, booking_id: int) -> list[EventKind]:
        """Return the kinds of the booking's events, oldest first."""
        return [e.kind for e in self.uow.state.booking_events.get(booking_id, [])]

"""Dispatch one booking of a contact-center group through ``CallService.ensure_task``.

Shared by the tests of the skill codes (ADR 0121 step 4b) and of the channel-independent task
contract (step 6): a group routed to Webex Contact Center, one booking made under a stored flow
revision, and the task the fake dispatcher received.
"""

from dataclasses import replace

from app.domain.admin import AdminService
from app.domain.booking import MeetingRef
from app.domain.call_service import CallService
from app.domain.calls import ContactCenterTask
from app.domain.flow_resolution import FlowRef
from app.domain.integration import IntegrationService
from app.domain.routing import RoutingChoice
from app.domain.scheduling import Organisation, ScheduleRule
from app.domain.service_flows import DispatchKind, HandlingAction, InteractionKind
from app.domain.services import BookingService
from app.domain.skills import Skill
from tests.fakes.clock import FixedClock
from tests.fakes.integrations import FakeDispatcher, FakeVideo, webex_cc
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import NOW, TEST_TOKEN_KEY, book, make_group, webex_routing, window
from tests.support.service_flows import legacy, revision

SKILLED_FLOW = FlowRef(50, 1)
"""A contact-center video flow that requires skill 10, and skill 11 for subject ``renewal``."""
LEGACY_FLOW = FlowRef(51, 1)
"""A legacy wrapper: video, no skills."""
TELEPHONE_FLOW = FlowRef(52, 1)
"""A contact-center telephone flow without skills: a kind Webex Contact Center does not carry."""


async def dispatch_one(
    *skills: Skill, flow: FlowRef | None, subject: str | None = None
) -> ContactCenterTask:
    """Dispatch one booking of a contact-center group made under ``flow``; return the task.

    Raises:
        DomainError: What ``ensure_task`` refuses (for example ``integration_not_ready``).
    """
    state = State(
        organisation_slugs={1: "demo"}, organisations={1: Organisation(1, "Demo", "demo")}
    )
    group = make_group(
        id=1,
        organisation_id=1,
        slug="balie",
        video_host_email="host@example.nl",
        contact_center=webex_routing("openplanner@example.biz", "openplanner-video", "team-1"),
    )
    state.groups[1] = group
    state.rules[1] = [ScheduleRule(weekday=0, window=window(capacity=1))]
    state.skills = {s.id or 0: s for s in skills}
    state.flow_revisions[(50, 1)] = revision(
        flow_id=50,
        interaction_kind=InteractionKind.VIDEO,
        handling_action=HandlingAction.OPEN_VIDEO,
        dispatch=DispatchKind.CONTACT_CENTER,
        required_skill_ids=frozenset({10}),
        subject_skill_ids={"renewal": frozenset({11})},
        published_at=NOW,
    )
    state.flow_revisions[(51, 1)] = legacy(flow_id=51, published_at=NOW)
    state.flow_revisions[(52, 1)] = revision(
        flow_id=52, dispatch=DispatchKind.CONTACT_CENTER, published_at=NOW
    )
    uow = InMemoryUnitOfWork(state)
    clock = FixedClock(NOW)
    receipt = await book(BookingService(uow, clock, TEST_TOKEN_KEY, treatment_buffer=0))
    booking = replace(
        receipt.booking,
        meeting=MeetingRef("m-1", "https://site.webex.com/m"),
        flow=flow,
        routing=RoutingChoice(subject) if subject else None,
    )
    dispatcher = FakeDispatcher()
    integration = IntegrationService(uow, clock, AdminService(uow, clock))
    calls = CallService(uow, integration, FakeVideo(), webex_cc(dispatcher), lambda b, g: "x")
    await calls.ensure_task(booking, group)
    [task] = dispatcher.items
    return task

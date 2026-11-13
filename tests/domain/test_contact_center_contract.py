"""The channel-independent contact-center task contract (ADR 0121 step 6).

A task names its interaction kind; each platform's descriptor declares what it carries; a flow
whose service's platform cannot carry its work is not ready, and a task it cannot carry is
refused before it is built. Webex Contact Center carries video and skills, nothing else yet.
"""

import pytest

from app.adapters.contact_centers import DESCRIPTORS
from app.adapters.wxcc import DESCRIPTOR
from app.domain.booking import MeetingRef
from app.domain.calls import ContactCenterTask, contact_center_task
from app.domain.contact_center import (
    INTERACTION_KIND,
    SKILL_CODES,
    ContactCenterCapabilities,
    ContactCenterKind,
    ProviderDescriptor,
    capabilities_of,
    uncarried,
)
from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_readiness import (
    ReadinessCheck,
    ReadinessReason,
    readiness,
)
from app.domain.service_flows import (
    DispatchKind,
    HandlingAction,
    InteractionKind,
    interaction_of,
)
from tests.support.contact_center_dispatch import (
    LEGACY_FLOW,
    TELEPHONE_FLOW,
    dispatch_one,
)
from tests.support.domain import webex_routing
from tests.support.flow_documents import (
    acme,
    advies,
    document_json,
    full,
    plan,
    published,
    refusals,
)
from tests.support.integration import GROUP, make_booking
from tests.support.service_flows import legacy, pool, revision

FLOW = "organisations[acme].groups[balie].flows[advies]"
WEBEX = DESCRIPTOR.capabilities
VIDEO_ONLY_NO_SKILLS = ContactCenterCapabilities(frozenset({InteractionKind.VIDEO}))
CONTACT_CENTER = DispatchKind.CONTACT_CENTER


def _reason(
    check: ReadinessCheck,
    carrier: ContactCenterCapabilities | None,
    **changes: object,
) -> ReadinessReason | None:
    """Return the reason of ``check`` for a contact-center revision with ``changes``."""
    made = revision(dispatch=CONTACT_CENTER, **changes)
    items = readiness(
        made,
        pool(guaranteed=frozenset({10})),
        offered_subjects=(),
        coded_skills=frozenset({10}),
        carrier=carrier,
    )
    return next(i for i in items if i.code is check).reason


VIDEO = {"interaction_kind": InteractionKind.VIDEO, "handling_action": HandlingAction.OPEN_VIDEO}


def test_webex_contact_center_declares_video_and_skills_only() -> None:
    assert WEBEX == ContactCenterCapabilities(frozenset({InteractionKind.VIDEO}), skills=True)


def test_a_descriptor_that_declares_nothing_carries_nothing() -> None:
    bare = ProviderDescriptor(ContactCenterKind.WEBEX_CC, "k", (), 10, "k.value")

    assert bare.capabilities == ContactCenterCapabilities()
    for kind in InteractionKind:
        assert uncarried(bare.capabilities, kind, skills=False) == INTERACTION_KIND


def test_uncarried_names_the_interaction_first_then_the_skills() -> None:
    assert uncarried(WEBEX, InteractionKind.VIDEO, skills=True) is None
    assert uncarried(WEBEX, InteractionKind.NONE, skills=True) == INTERACTION_KIND
    assert uncarried(VIDEO_ONLY_NO_SKILLS, InteractionKind.VIDEO, skills=False) is None
    assert uncarried(VIDEO_ONLY_NO_SKILLS, InteractionKind.VIDEO, skills=True) == SKILL_CODES


def test_only_a_complete_routing_carries_anything() -> None:
    assert capabilities_of(webex_routing()) == WEBEX
    assert capabilities_of(webex_routing(channel=None)) is None
    assert capabilities_of(None) is None


def test_a_contact_center_flow_without_a_routing_is_not_ready() -> None:
    reason = _reason(ReadinessCheck.DISPATCH, None, **VIDEO)
    assert reason is ReadinessReason.CONTACT_CENTER_MISSING


@pytest.mark.parametrize(
    "kind",
    [InteractionKind.TELEPHONE, InteractionKind.IN_PERSON, InteractionKind.NONE],
)
def test_a_flow_whose_interaction_the_platform_cannot_carry_is_not_ready(
    kind: InteractionKind,
) -> None:
    action = {
        InteractionKind.TELEPHONE: HandlingAction.CALL,
        InteractionKind.IN_PERSON: HandlingAction.VIEW_REQUEST,
        InteractionKind.NONE: HandlingAction.VIEW_REQUEST,
    }[kind]
    reason = _reason(ReadinessCheck.DISPATCH, WEBEX, interaction_kind=kind, handling_action=action)
    assert reason is ReadinessReason.INTERACTION_NOT_CARRIED


def test_a_carried_flow_waits_only_for_what_follows() -> None:
    """Video on Webex is carried; contact-center dispatch of a new flow follows in step 6b."""
    assert _reason(ReadinessCheck.DISPATCH, WEBEX, **VIDEO) is ReadinessReason.NOT_BUILT_YET


def test_skills_need_a_platform_that_routes_on_them() -> None:
    skilled = {**VIDEO, "required_skill_ids": frozenset({10})}

    assert _reason(ReadinessCheck.SKILLS, VIDEO_ONLY_NO_SKILLS, **skilled) is (
        ReadinessReason.SKILLS_NOT_CARRIED
    )
    assert _reason(ReadinessCheck.SKILLS, WEBEX, **skilled) is None
    assert _reason(ReadinessCheck.SKILLS, VIDEO_ONLY_NO_SKILLS, **VIDEO) is None


def test_the_own_queue_and_a_legacy_flow_are_not_judged_by_a_platform() -> None:
    for made in (
        revision(dispatch=DispatchKind.OWN_QUEUE),
        legacy(dispatch=CONTACT_CENTER),
    ):
        for carrier in (None, ContactCenterCapabilities()):
            items = readiness(made, pool(), offered_subjects=(), carrier=carrier)
            reasons = {item.reason for item in items}
            assert not reasons & {
                ReadinessReason.CONTACT_CENTER_MISSING,
                ReadinessReason.INTERACTION_NOT_CARRIED,
                ReadinessReason.SKILLS_NOT_CARRIED,
            }


MEETING = MeetingRef("m-1", "https://site.webex.com/meet/abc")


def test_a_task_names_its_interaction_and_a_booking_without_a_flow_is_video() -> None:
    task = contact_center_task(make_booking(meeting=MEETING), GROUP, start_label="x")

    assert task.interaction is InteractionKind.VIDEO


def test_a_task_the_platform_cannot_carry_is_refused_before_it_is_built() -> None:
    booking = make_booking(meeting=MEETING)

    with pytest.raises(DomainError) as excinfo:
        contact_center_task(booking, GROUP, start_label="x", interaction=InteractionKind.NONE)

    assert (excinfo.value.code, excinfo.value.field) == (
        ErrorCode.INTEGRATION_NOT_READY,
        INTERACTION_KIND,
    )


async def test_the_dispatch_takes_the_interaction_kind_from_the_recorded_flow() -> None:
    assert (await dispatch_one(flow=None)).interaction is InteractionKind.VIDEO
    assert (await dispatch_one(flow=LEGACY_FLOW)).interaction is InteractionKind.VIDEO
    with pytest.raises(DomainError) as excinfo:
        await dispatch_one(flow=TELEPHONE_FLOW)
    assert (excinfo.value.code, excinfo.value.field) == (
        ErrorCode.INTEGRATION_NOT_READY,
        INTERACTION_KIND,
    )


def test_a_task_carries_no_field_for_a_phone_number() -> None:
    """A telephone interaction never adds the participant's number to the contract."""
    fields = set(ContactCenterTask.__dataclass_fields__)
    assert not {name for name in fields if "phone" in name or "number" in name}


PLATFORMS = {kind.value: descriptor for kind, descriptor in DESCRIPTORS.items()}
WEBEX_ROUTING = {
    "kind": "webex_cc",
    "attributes": {"business_address": "openplanner@example.biz", "channel": "video"},
}


def _contact_center_document(routing: object) -> dict[str, object]:
    """Return the round-trip document with ``advies`` revision 3 published for the contact center.

    The service ``balie`` gets ``routing`` as its contact-center routing (``None``: none).
    """
    data = document_json()
    third = advies(data)["revisions"][2]
    third.update(
        published_at=published(3),
        interaction_kind="video",
        handling_action="open_video",
        dispatch="contact_center",
    )
    advies(data)["current_revision"], advies(data)["draft_revision"] = 3, None
    if routing is not None:
        acme(data)["groups"][0]["contact_center"] = routing
    return data


def test_a_configuration_document_judges_with_the_routing_of_the_service() -> None:
    """A newly published contact-center revision is held to the service's own routing.

    Without a routing the checklist has a point the administrator can fix, so the publication
    is refused, as in the portal; with a complete Webex routing what is left only follows.
    """
    without = plan(_contact_center_document(None), full(), contact_centers=PLATFORMS)
    incomplete = {"kind": "webex_cc", "attributes": {"channel": "video"}}
    half = plan(_contact_center_document(incomplete), full(), contact_centers=PLATFORMS)
    routed = plan(_contact_center_document(WEBEX_ROUTING), full(), contact_centers=PLATFORMS)

    refused = (f"{FLOW}.revisions[3]", ErrorCode.INVALID_CONFIGURATION, "readiness")
    assert refused in refusals(without)
    assert refused in refusals(half)
    assert refused not in refusals(routed)


def test_a_recorded_flow_gives_its_own_interaction_and_never_a_guess() -> None:
    assert interaction_of(None, recorded=False) is InteractionKind.VIDEO
    assert interaction_of(revision(), recorded=True) is InteractionKind.TELEPHONE
    for broken in (None, revision(interaction_kind=None)):
        with pytest.raises(DomainError) as excinfo:
            interaction_of(broken, recorded=True)
        assert (excinfo.value.code, excinfo.value.field) == (
            ErrorCode.INTEGRATION_NOT_READY,
            INTERACTION_KIND,
        )

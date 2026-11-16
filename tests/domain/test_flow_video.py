"""Video for a new flow (ADR 0121 row 36, checklist G42).

A new flow may be a video call: its draft is accepted, its readiness asks for the service's
video host (``video_host_missing``, an administrator can fix it) and its bookings are request
work like any other, with one meeting each. ``has_video`` is the one rule of which booking is a
video call: no flow, a legacy flow or a video flow; a new flow of another kind never (A19).
"""

import pytest

from app.domain.errors import ErrorCode
from app.domain.flow_readiness import (
    FOLLOWING,
    ReadinessCheck,
    ReadinessReason,
    ReadinessState,
    can_activate,
    readiness,
    readiness_state,
)
from app.domain.service_flows import (
    BUILT_KINDS,
    COMBINATIONS,
    FlowRevision,
    HandlingAction,
    InteractionKind,
    ReadinessPolicy,
    check_built_choices,
    check_draft_content,
    has_video,
)
from app.domain.service_requests import is_request_work, needs_check_in, without_video
from tests.support.flow_documents import acme, advies, document_json, empty_flows, plan, refusals
from tests.support.pool_flows import open_gate, shut_gate
from tests.support.service_flows import legacy, pool, revision

FLOW = "organisations[acme].groups[balie].flows[advies]"


def video(**changes: object) -> FlowRevision:
    """Return a complete new video flow on the own queue."""
    return revision(
        interaction_kind=InteractionKind.VIDEO,
        handling_action=HandlingAction.OPEN_VIDEO,
        **changes,
    )


def dispatch_reason(rev: FlowRevision, *, video_host: bool) -> ReadinessReason | None:
    """Return the reason of the dispatch point of ``rev``'s checklist."""
    items = readiness(rev, pool(), offered_subjects=(), own_queue=True, video_host=video_host)
    return next(i for i in items if i.code is ReadinessCheck.DISPATCH).reason


def test_every_interaction_kind_is_built_and_a_video_draft_is_accepted() -> None:
    assert InteractionKind.VIDEO in BUILT_KINDS
    check_built_choices(video())
    check_draft_content(video())
    assert COMBINATIONS[InteractionKind.VIDEO].readiness is ReadinessPolicy.MEDIA_PRESENCE


def test_a_video_flow_on_a_service_with_a_video_host_can_be_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_gate(monkeypatch)
    items = readiness(video(), pool(), offered_subjects=(), own_queue=True, video_host=True)

    assert can_activate(items)
    assert readiness_state(items) is ReadinessState.COMPLETE


def test_a_video_flow_needs_the_services_video_host_which_an_administrator_can_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_gate(monkeypatch)
    items = readiness(video(), pool(), offered_subjects=(), own_queue=True, video_host=False)

    assert dispatch_reason(video(), video_host=False) is ReadinessReason.VIDEO_HOST_MISSING
    assert not can_activate(items)
    assert ReadinessReason.VIDEO_HOST_MISSING not in FOLLOWING
    assert readiness_state(items) is ReadinessState.INCOMPLETE


def test_the_video_host_matters_only_to_a_new_video_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flow of another kind and a legacy flow (its service's own settings) never ask for it."""
    for gate in (shut_gate, open_gate):
        gate(monkeypatch)
        assert dispatch_reason(revision(), video_host=False) is None
        assert dispatch_reason(legacy(), video_host=False) is None


def test_a_shut_gate_still_names_only_rollback_not_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    shut_gate(monkeypatch)
    items = readiness(video(), pool(), offered_subjects=(), own_queue=True, video_host=True)

    assert [i.reason for i in items if not i.ok] == [ReadinessReason.ROLLBACK_NOT_SAFE]


def test_which_booking_is_a_video_call() -> None:
    assert has_video(None, recorded=False)  # no flow: as before the train
    assert has_video(legacy(), recorded=True)
    assert has_video(video(), recorded=True)
    assert has_video(None, recorded=True)  # an unreadable revision keeps its link
    for kind in (InteractionKind.TELEPHONE, InteractionKind.IN_PERSON, InteractionKind.NONE):
        assert not has_video(revision(interaction_kind=kind), recorded=True)


def test_a_video_flow_is_request_work_with_video_and_without_a_check_in() -> None:
    """Media presence is the readiness: claimed from the start, never checked in."""
    assert is_request_work(video())
    assert not without_video(video())
    assert not needs_check_in(video())
    assert without_video(revision(interaction_kind=InteractionKind.NONE))
    assert not is_request_work(legacy())


def test_a_document_cannot_activate_a_video_flow_on_a_service_without_a_video_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one rule behind the portal also refuses the import, with the same point."""
    open_gate(monkeypatch)
    data = document_json()
    flow = advies(data)
    flow["state"] = "active"
    for rev in flow["revisions"]:
        rev["interaction_kind"] = "video"
        rev["handling_action"] = "open_video"
    service = acme(data)["groups"][0]
    service["dispatch_kind"] = "openplanner_queue"
    reason = (FLOW, ErrorCode.INVALID_CONFIGURATION, "readiness_dispatch")

    assert reason in refusals(plan(data, empty_flows()))

    service["video_host_email"] = "host@voorbeeld.nl"
    assert reason not in refusals(plan(data, empty_flows()))

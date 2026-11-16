"""The own-queue rule: a flow on the own queue needs a service with one (ADR 0121).

Product owner, 2026-11-15: without an own queue nobody can claim the work of a flow that
dispatches there, and the watchdog expires it, so activation is refused in the one readiness
check the portal and the configuration import share.
"""

import pytest

from app.domain.contact_center import ContactCenterCapabilities
from app.domain.errors import ErrorCode
from app.domain.flow_readiness import (
    FOLLOWING,
    ReadinessCheck,
    ReadinessReason,
    ReadinessState,
    can_activate,
    can_publish,
    readiness,
    readiness_state,
)
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    InteractionKind,
)
from tests.support.flow_documents import acme, advies, document_json, empty_flows, plan, refusals
from tests.support.pool_flows import open_gate, shut_gate
from tests.support.service_flows import legacy, pool, revision

FLOW = "organisations[acme].groups[balie].flows[advies]"
CARRIER = ContactCenterCapabilities(frozenset({InteractionKind.TELEPHONE}), skills=True)


def rollback_reason(rev: FlowRevision, *, own_queue: bool) -> ReadinessReason | None:
    """Return the reason of the rollback point of ``rev``'s checklist."""
    items = readiness(rev, pool(), offered_subjects=(), carrier=CARRIER, own_queue=own_queue)
    return next(i for i in items if i.code is ReadinessCheck.ROLLBACK).reason


def test_an_open_gate_refuses_an_own_queue_flow_on_a_service_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_gate(monkeypatch)
    items = readiness(revision(), pool(), offered_subjects=(), own_queue=False)

    assert not can_activate(items)
    assert rollback_reason(revision(), own_queue=False) is ReadinessReason.OWN_QUEUE_MISSING


def test_the_administrator_can_fix_it_so_the_checklist_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a part that follows: choosing the own queue for the service fixes it."""
    open_gate(monkeypatch)
    items = readiness(revision(), pool(), offered_subjects=(), own_queue=False)

    assert ReadinessReason.OWN_QUEUE_MISSING not in FOLLOWING
    assert readiness_state(items) is ReadinessState.INCOMPLETE
    assert not can_publish(items)


def test_an_own_queue_flow_on_a_service_with_one_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_gate(monkeypatch)
    assert rollback_reason(revision(), own_queue=True) is None
    assert can_activate(readiness(revision(), pool(), offered_subjects=(), own_queue=True))


def test_a_shut_gate_names_only_rollback_not_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate-shut behaviour is unchanged: the rollback gate is named, and only it."""
    shut_gate(monkeypatch)
    for own_queue in (False, True):
        items = readiness(revision(), pool(), offered_subjects=(), own_queue=own_queue)
        assert [i.reason for i in items if not i.ok] == [ReadinessReason.ROLLBACK_NOT_SAFE]
        assert readiness_state(items) is ReadinessState.COMPLETE_EXCEPT_FOLLOWING


def test_a_contact_center_flow_keeps_its_own_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service's own queue does not matter to a flow that dispatches to the contact center."""
    open_gate(monkeypatch)
    made = revision(
        dispatch=DispatchKind.CONTACT_CENTER, completion=CompletionPolicy.PROVIDER_EVENT
    )
    for own_queue in (False, True):
        reason = rollback_reason(made, own_queue=own_queue)
        assert reason is ReadinessReason.CONTACT_CENTER_NOT_READY
    monkeypatch.setattr("app.domain.service_flows.CONTACT_CENTER_FLOWS_READY", True)
    assert rollback_reason(made, own_queue=False) is None


def test_a_legacy_own_queue_flow_is_never_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An already active legacy flow follows its service's settings, queue or not."""
    for gate in (shut_gate, open_gate):
        gate(monkeypatch)
        assert rollback_reason(legacy(), own_queue=False) is None


def test_a_document_cannot_activate_an_own_queue_flow_on_a_service_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one rule behind the portal also refuses the import, with the same reason."""
    open_gate(monkeypatch)
    data = document_json()
    advies(data)["state"] = "active"
    service = acme(data)["groups"][0]
    reason = (FLOW, ErrorCode.INVALID_CONFIGURATION, "readiness_rollback")

    service["dispatch_kind"] = "contact_center"
    assert reason in refusals(plan(data, empty_flows()))

    service["dispatch_kind"] = "openplanner_queue"
    assert reason not in refusals(plan(data, empty_flows()))

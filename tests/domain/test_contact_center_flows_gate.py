"""The contact-center rule: no contact-center flow goes live before G13 to G15 (ADR 0121)."""

import pytest

from app.domain.contact_center import ContactCenterCapabilities
from app.domain.flow_readiness import (
    FOLLOWING,
    ReadinessCheck,
    ReadinessReason,
    can_activate,
    readiness,
)
from app.domain.service_flows import (
    CONTACT_CENTER_FLOWS_READY,
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    InteractionKind,
)
from tests.support.pool_flows import open_gate, shut_gate
from tests.support.service_flows import legacy, pool, revision

CARRIER = ContactCenterCapabilities(frozenset({InteractionKind.NONE}), skills=True)


def contact_center() -> FlowRevision:
    """Return a non-legacy revision that dispatches to the contact center."""
    return revision(
        dispatch=DispatchKind.CONTACT_CENTER, completion=CompletionPolicy.PROVIDER_EVENT
    )


def rollback_reason(rev: FlowRevision) -> ReadinessReason | None:
    """Return the reason of the rollback point of ``rev``'s checklist."""
    items = readiness(rev, pool(), offered_subjects=(), carrier=CARRIER)
    return next(i for i in items if i.code is ReadinessCheck.ROLLBACK).reason


def test_the_contact_center_rule_is_a_fixed_constant_that_starts_shut() -> None:
    assert CONTACT_CENTER_FLOWS_READY is False
    assert ReadinessReason.CONTACT_CENTER_NOT_READY in FOLLOWING


def test_a_shut_gate_behaves_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    shut_gate(monkeypatch)
    assert rollback_reason(contact_center()) is ReadinessReason.ROLLBACK_NOT_SAFE
    assert rollback_reason(revision()) is ReadinessReason.ROLLBACK_NOT_SAFE


def test_an_open_gate_refuses_a_contact_center_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)
    items = readiness(contact_center(), pool(), offered_subjects=(), carrier=CARRIER)
    assert not can_activate(items)
    assert rollback_reason(contact_center()) is ReadinessReason.CONTACT_CENTER_NOT_READY


def test_an_open_gate_allows_an_own_queue_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)
    assert rollback_reason(revision()) is None


def test_a_legacy_contact_center_flow_is_never_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    for gate in (shut_gate, open_gate):
        gate(monkeypatch)
        assert rollback_reason(legacy(dispatch=DispatchKind.CONTACT_CENTER)) is None


def test_the_flip_that_closes_g13_to_g15_allows_the_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    open_gate(monkeypatch)
    monkeypatch.setattr("app.domain.service_flows.CONTACT_CENTER_FLOWS_READY", True)
    assert rollback_reason(contact_center()) is None

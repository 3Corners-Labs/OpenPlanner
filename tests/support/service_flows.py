"""Builders for skills, capacity pools and flow revisions shared by the ADR 0121 domain tests."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.domain.capacity import CapacityPool, PoolMode
from app.domain.service_flows import (
    CompletionPolicy,
    DispatchKind,
    FlowRevision,
    FlowState,
    HandlingAction,
    IntakeMode,
    InteractionKind,
    ServiceFlow,
    legacy_revision,
)
from app.domain.skills import Skill
from tests.support.domain import NOW

LABELS = {"nl": "Balie", "en": "Desk"}


def skill(
    code: str = "passport",
    labels: Mapping[str, str] | None = None,
    archived_at: datetime | None = None,
) -> Skill:
    """Return a skill of organisation 1."""
    return Skill(
        None,
        1,
        code,
        {"nl": "Paspoort", "en": "Passport"} if labels is None else labels,
        archived_at,
    )


def pool(
    name: str = "Loket",
    mode: PoolMode = PoolMode.COUNTED,
    guaranteed: frozenset[int] = frozenset(),
    archived_at: datetime | None = None,
) -> CapacityPool:
    """Return a pool of organisation 1."""
    return CapacityPool(1, 1, name, mode, guaranteed, archived_at)


def revision(**changes: Any) -> FlowRevision:
    """Return a complete, non-legacy telephone revision; ``changes`` override fields."""
    base = FlowRevision(
        flow_id=1,
        revision=1,
        labels=LABELS,
        intake_modes=frozenset({IntakeMode.APPOINTMENT}),
        interaction_kind=InteractionKind.TELEPHONE,
        duration_minutes=20,
        capacity_pool_id=1,
        required_skill_ids=frozenset(),
        subject_skill_ids={},
        dispatch=DispatchKind.OWN_QUEUE,
        handling_action=HandlingAction.CALL,
        completion=CompletionPolicy.OPERATOR,
    )
    return replace(base, **changes)


def legacy(**changes: Any) -> FlowRevision:
    """Return a legacy revision; ``changes`` override fields."""
    base = legacy_revision(
        labels=LABELS,
        intake_mode=IntakeMode.APPOINTMENT,
        duration_minutes=20,
        capacity_pool_id=1,
        dispatch=DispatchKind.OWN_QUEUE,
    )
    return replace(base, **changes)


def flow(
    code: str = "a",
    state: FlowState = FlowState.ACTIVE,
    flow_id: int | None = None,
    default_for: frozenset[IntakeMode] = frozenset(),
    current: FlowRevision | None = None,
    draft: FlowRevision | None = None,
) -> ServiceFlow:
    """Return a flow of group 1; with neither ``current`` nor ``draft`` it has a current one.

    A stored flow (``flow_id`` set) needs a published current revision, so the default one is
    published then.
    """
    if current is None and draft is None:
        current = revision(published_at=NOW if flow_id is not None else None)
    return ServiceFlow(flow_id, 1, code, state, default_for, current, draft)

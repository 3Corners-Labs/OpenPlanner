"""The legacy pool and flows every group has (ADR 0121 step 2e): every branch of the rule."""

from dataclasses import replace
from datetime import time

import pytest

from app.domain.capacity import PoolMode
from app.domain.default_flows import (
    APPOINTMENT_CODE,
    DIRECT_CODE,
    UNSTORED_POOL,
    DefaultFlowSet,
    StoredFlow,
    bind_pool,
    default_flow_set,
    flow_dispatch,
    missing_defaults,
    offers_direct,
    stored_flow,
)
from app.domain.scheduling import DispatchKind, Offer, ScheduleRule, TimeWindow
from app.domain.service_flows import DispatchKind as FlowDispatch
from app.domain.service_flows import FlowState, IntakeMode


def wanted(
    *,
    name: str = "Balie",
    dispatch: DispatchKind = DispatchKind.CONTACT_CENTER,
    direct: bool = False,
    archived: bool = False,
) -> DefaultFlowSet:
    """Return the defaults of group 7 of organisation 3."""
    return default_flow_set(7, 3, name, 20, dispatch, direct, archived)


def rule(offer: Offer, capacity: int) -> ScheduleRule:
    """Return a Monday rule with ``offer`` and ``capacity``."""
    return ScheduleRule(0, TimeWindow(time(9), time(10), capacity, None, offer))


def test_a_group_has_a_legacy_pool_and_an_appointment_flow() -> None:
    result = wanted()

    assert result.pool is not None
    assert (result.pool.organisation_id, result.pool.group_id) == (3, 7)
    assert (result.pool.name, result.pool.mode, result.pool.id) == (
        "Balie",
        PoolMode.LEGACY_GROUP,
        None,
    )
    (flow,) = result.flows
    assert (flow.code, flow.state, flow.default_for) == (
        APPOINTMENT_CODE,
        FlowState.ACTIVE,
        frozenset({IntakeMode.APPOINTMENT}),
    )
    assert flow.current is not None
    assert flow.current.legacy
    assert flow.current.duration_minutes == 20
    assert flow.current.capacity_pool_id == UNSTORED_POOL
    assert flow.current.labels == {"nl": "Balie", "en": "Balie"}


def test_a_group_that_offers_the_direct_call_has_a_direct_flow_too() -> None:
    codes = [(f.code, f.default_for) for f in wanted(direct=True).flows]

    assert codes == [
        (APPOINTMENT_CODE, frozenset({IntakeMode.APPOINTMENT})),
        (DIRECT_CODE, frozenset({IntakeMode.ON_DEMAND})),
    ]


def test_an_archived_group_gets_archived_flows() -> None:
    assert {f.state for f in wanted(direct=True, archived=True).flows} == {FlowState.ARCHIVED}


@pytest.mark.parametrize(
    ("kind", "dispatch"),
    [
        (DispatchKind.OPENPLANNER_QUEUE, FlowDispatch.OWN_QUEUE),
        (DispatchKind.CONTACT_CENTER, FlowDispatch.CONTACT_CENTER),
    ],
)
def test_the_dispatch_follows_the_group(kind: DispatchKind, dispatch: FlowDispatch) -> None:
    assert flow_dispatch(kind) is dispatch
    assert {f.current.dispatch for f in wanted(dispatch=kind).flows if f.current} == {dispatch}


def test_a_long_name_is_cut_as_the_migration_cuts_it() -> None:
    long = "x" * 79 + "  y" + "z" * 30

    result = wanted(name=f"  {long}  ")

    assert result.pool is not None
    assert result.pool.name == "x" * 79
    assert result.flows[0].current is not None
    assert result.flows[0].current.labels["nl"] == "x" * 79


def test_a_name_of_blanks_after_the_cut_becomes_a_dash() -> None:
    assert wanted(name=" " * 3 + "a").pool is not None
    assert wanted(name=" ").pool is not None  # blank to ``strip`` but never empty
    assert wanted(name=" ").pool.name == "-"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("offer", "capacity", "expected"),
    [
        (Offer.SCHEDULED, 2, False),
        (Offer.DIRECT, 1, True),
        (Offer.BOTH, 1, True),
        (Offer.DIRECT, 0, False),
        (Offer.BOTH, 0, False),
    ],
)
def test_the_direct_call_is_offered_by_a_direct_rule_with_agents(
    offer: Offer, capacity: int, expected: bool
) -> None:
    assert offers_direct([rule(Offer.SCHEDULED, 1), rule(offer, capacity)]) is expected


def test_no_rules_offer_no_direct_call() -> None:
    assert offers_direct([]) is False


def test_a_new_group_lacks_everything() -> None:
    missing = missing_defaults(wanted(direct=True), pool_exists=False, existing=[])

    assert missing == wanted(direct=True)
    assert not missing.empty


def test_a_group_that_has_it_all_lacks_nothing() -> None:
    have = [stored_flow(f) for f in wanted(direct=True).flows]

    missing = missing_defaults(wanted(direct=True), pool_exists=True, existing=have)

    assert missing.empty
    assert missing.pool is None


def test_a_second_run_creates_nothing() -> None:
    first = missing_defaults(wanted(direct=True), False, [])

    second = missing_defaults(wanted(direct=True), True, [stored_flow(f) for f in first.flows])

    assert second.empty


def test_only_what_is_missing_is_created() -> None:
    have = [StoredFlow(APPOINTMENT_CODE, FlowState.ACTIVE, frozenset({IntakeMode.APPOINTMENT}))]

    missing = missing_defaults(wanted(direct=True), pool_exists=True, existing=have)

    assert missing.pool is None
    assert [f.code for f in missing.flows] == [DIRECT_CODE]


def test_a_flow_that_exists_in_any_state_is_never_created_again() -> None:
    have = [
        StoredFlow(APPOINTMENT_CODE, FlowState.ARCHIVED, frozenset()),
        StoredFlow(DIRECT_CODE, FlowState.DRAFT, frozenset()),
    ]

    assert missing_defaults(wanted(direct=True), True, have).empty


def test_a_direct_flow_stays_when_the_group_stops_offering_the_direct_call() -> None:
    have = [stored_flow(f) for f in wanted(direct=True).flows]

    missing = missing_defaults(wanted(direct=False), pool_exists=True, existing=have)

    assert missing.empty  # nothing is deleted; the schedule decides whether a page is open


def test_a_group_without_a_pool_but_with_flows_gets_the_pool() -> None:
    have = [stored_flow(f) for f in wanted().flows]

    missing = missing_defaults(wanted(), pool_exists=False, existing=have)

    assert missing.pool is not None
    assert missing.flows == ()


def test_a_flow_does_not_take_a_default_path_another_active_flow_holds() -> None:
    have = [StoredFlow("other", FlowState.ACTIVE, frozenset({IntakeMode.APPOINTMENT}))]

    (flow,) = missing_defaults(wanted(), True, have).flows

    assert flow.code == APPOINTMENT_CODE
    assert flow.default_for == frozenset()


def test_an_archived_flow_does_not_hold_a_default_path() -> None:
    have = [StoredFlow("other", FlowState.ARCHIVED, frozenset({IntakeMode.APPOINTMENT}))]

    (flow,) = missing_defaults(wanted(), True, have).flows

    assert flow.default_for == frozenset({IntakeMode.APPOINTMENT})


def test_the_flows_of_an_archived_group_keep_their_defaults() -> None:
    have = [StoredFlow("other", FlowState.ACTIVE, frozenset({IntakeMode.APPOINTMENT}))]

    (flow,) = missing_defaults(wanted(archived=True), True, have).flows

    assert flow.default_for == frozenset({IntakeMode.APPOINTMENT})  # no index applies to archived


def test_binding_names_the_stored_pool_in_every_revision() -> None:
    bound = bind_pool(wanted(direct=True), 42)

    assert {f.current.capacity_pool_id for f in bound.flows if f.current} == {42}
    assert bound.pool == wanted(direct=True).pool


def test_an_empty_set_is_empty() -> None:
    assert DefaultFlowSet(None, ()).empty
    assert not replace(DefaultFlowSet(None, ()), flows=wanted().flows).empty

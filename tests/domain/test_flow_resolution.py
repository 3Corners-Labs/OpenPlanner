"""Resolving the flow of a new booking (ADR 0121 step 3a): default and explicit, no fallback."""

from dataclasses import replace

import pytest

from app.domain.errors import DomainError, ErrorCode
from app.domain.flow_resolution import FlowRef, resolve_default, resolve_explicit
from app.domain.service_flows import FlowState, IntakeMode, InteractionKind, ServiceFlow
from tests.support.domain import NOW
from tests.support.service_flows import flow, legacy, revision

APPOINTMENT = frozenset({IntakeMode.APPOINTMENT})
ON_DEMAND = frozenset({IntakeMode.ON_DEMAND})


def stored(
    code: str,
    flow_id: int,
    state: FlowState = FlowState.ACTIVE,
    default_for: frozenset[IntakeMode] = APPOINTMENT,
    modes: frozenset[IntakeMode] = APPOINTMENT,
) -> ServiceFlow:
    """Return a stored, published flow offering ``modes``, the default of ``default_for``."""
    current = revision(flow_id=flow_id, intake_modes=modes, published_at=NOW)
    return flow(code, state, flow_id, default_for, current)


def refused(call: object) -> DomainError:
    """Run ``call`` and return the domain error it raises."""
    assert callable(call)
    with pytest.raises(DomainError) as error:
        call()
    return error.value


def test_the_default_is_the_active_flow_of_the_path() -> None:
    flows = [stored("other", 1, default_for=frozenset()), stored("main", 2)]

    resolved = resolve_default(flows, IntakeMode.APPOINTMENT)

    assert (resolved.flow_id, resolved.code, resolved.revision) == (2, "main", 1)
    assert resolved.interaction_kind is InteractionKind.TELEPHONE
    assert resolved.ref == FlowRef(2, 1)


def test_the_revision_in_effect_is_the_current_one_not_the_draft() -> None:
    current = revision(flow_id=2, revision=3, published_at=NOW)
    draft = revision(flow_id=2, revision=4)
    flows = [flow("main", flow_id=2, default_for=APPOINTMENT, current=current, draft=draft)]

    assert resolve_default(flows, IntakeMode.APPOINTMENT).revision == 3


def test_the_default_of_each_path_is_its_own() -> None:
    flows = [
        stored("appointment", 1),
        stored("direct", 2, default_for=ON_DEMAND, modes=ON_DEMAND),
    ]

    assert resolve_default(flows, IntakeMode.ON_DEMAND).code == "direct"
    assert resolve_default(flows, IntakeMode.APPOINTMENT).code == "appointment"


def test_an_archived_flow_is_never_the_default() -> None:
    error = refused(
        lambda: resolve_default([stored("main", 1, FlowState.ARCHIVED)], IntakeMode.APPOINTMENT)
    )

    assert error.code is ErrorCode.FLOW_DEFAULT_MISSING


def test_no_default_for_the_path_is_an_error() -> None:
    error = refused(lambda: resolve_default([stored("main", 1)], IntakeMode.ON_DEMAND))

    assert (error.code, error.field) == (ErrorCode.FLOW_DEFAULT_MISSING, "on_demand")


def test_two_defaults_for_one_path_are_an_error() -> None:
    error = refused(
        lambda: resolve_default([stored("a", 1), stored("b", 2)], IntakeMode.APPOINTMENT)
    )

    assert error.code is ErrorCode.FLOW_DEFAULT_AMBIGUOUS


def test_the_labels_of_a_flow_change_nothing() -> None:
    """A01: a name is display text; resolution never reads it."""
    named = revision(flow_id=1, labels={"nl": "Heel anders", "en": "Quite different"})
    named = replace(named, published_at=NOW)
    flows = [flow("main", flow_id=1, default_for=APPOINTMENT, current=named)]

    assert resolve_default(flows, IntakeMode.APPOINTMENT).code == "main"
    assert resolve_explicit(flows, "main", IntakeMode.APPOINTMENT).flow_id == 1


def test_an_explicit_code_resolves_to_that_flow_default_or_not() -> None:
    flows = [stored("main", 1), stored("second", 2, default_for=frozenset())]

    resolved = resolve_explicit(flows, "second", IntakeMode.APPOINTMENT)

    assert (resolved.flow_id, resolved.code) == (2, "second")


@pytest.mark.parametrize("code", ["unknown", "MAIN", "", "main "])
def test_an_unknown_code_is_refused_without_a_fallback(code: str) -> None:
    """A04: an explicit flow that does not resolve is never replaced by the default."""
    flows = [stored("main", 1)]
    assert resolve_default(flows, IntakeMode.APPOINTMENT).code == "main"

    error = refused(lambda: resolve_explicit(flows, code, IntakeMode.APPOINTMENT))

    assert (error.code, error.field) == (ErrorCode.FLOW_NOT_AVAILABLE, "flow")


def test_an_archived_or_draft_flow_is_refused() -> None:
    archived = stored("old", 1, FlowState.ARCHIVED, frozenset())
    draft = flow(
        "new", FlowState.DRAFT, None, frozenset(), None, revision(flow_id=None, published_at=None)
    )

    for code in ("old", "new"):
        error = refused(
            lambda c=code: resolve_explicit([archived, draft], c, IntakeMode.APPOINTMENT)
        )
        assert error.code is ErrorCode.FLOW_NOT_AVAILABLE


def test_a_flow_that_does_not_offer_the_path_is_refused() -> None:
    direct = [stored("direct", 2, default_for=ON_DEMAND, modes=ON_DEMAND)]

    assert resolve_explicit(direct, "direct", IntakeMode.ON_DEMAND).flow_id == 2
    error = refused(lambda: resolve_explicit(direct, "direct", IntakeMode.APPOINTMENT))
    assert error.code is ErrorCode.FLOW_NOT_AVAILABLE


def test_a_flow_of_another_service_is_unknown() -> None:
    """A05: the resolver only sees the flows it is given, so a foreign code is just unknown."""
    own = [stored("main", 1)]

    error = refused(lambda: resolve_explicit(own, "elsewhere", IntakeMode.APPOINTMENT))

    assert error.code is ErrorCode.FLOW_NOT_AVAILABLE


def test_a_legacy_flow_resolves_like_any_other() -> None:
    wrapper = flow(
        "appointment",
        flow_id=1,
        default_for=APPOINTMENT,
        current=replace(legacy(flow_id=1), published_at=NOW),
    )

    assert resolve_explicit([wrapper], "appointment", IntakeMode.APPOINTMENT).legacy

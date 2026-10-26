"""Shared principals and in-memory state for the admin use-case tests (planning, holidays).

Two organisations with one group each: ``demo`` (the admins' own) and ``andere`` (someone
else's), so every test can check both sides of the matrix.
"""

from app.domain.audit import Outcome
from app.domain.authorization import Principal, Role
from app.domain.scheduling import Organisation
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import make_group

SUPER = Principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = Principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
GROUP_ADMIN = Principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
OUTSIDER = Principal("x-1", "X", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), frozenset())


def two_organisations(next_id: int = 1) -> State:
    """Return a state with organisations ``demo`` and ``andere`` and one group in each."""
    state = State(next_id=next_id)
    state.organisation_slugs = {1: "demo", 2: "andere"}
    state.organisations = {
        1: Organisation(1, "Demo-gemeente", "demo"),
        2: Organisation(2, "Andere gemeente", "andere"),
    }
    state.groups[1] = make_group(id=1, organisation_id=1, slug="demo", name="Demo")
    state.groups[2] = make_group(id=2, organisation_id=2, slug="andere-team", name="Andere")
    return state


def audit_rows(uow: InMemoryUnitOfWork) -> list[tuple[str, str, Outcome, str, str | None]]:
    """Return (actor, action, outcome, detail, group) of every audit event, oldest first."""
    return [(e.actor, e.action, e.outcome, e.detail, e.group) for e in uow.state.audit]

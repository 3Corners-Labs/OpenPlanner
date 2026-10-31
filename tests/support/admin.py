"""Shared principals and in-memory state for the admin use-case tests (planning, holidays).

Two organisations with one group each: ``demo`` (the admins' own) and ``andere`` (someone
else's), so every test can check both sides of the matrix.
"""

from app.domain.audit import Outcome
from app.domain.authorization import Role
from app.domain.scheduling import Organisation
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.support.domain import make_group
from tests.support.principals import principal

SUPER = principal("s-1", "S", frozenset({Role.SUPERUSER}), frozenset(), frozenset())
ORG_ADMIN = principal("o-1", "O", frozenset({Role.ORG_ADMIN}), frozenset({"demo"}), frozenset())
GROUP_ADMIN = principal("g-1", "G", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"demo"}))
OUTSIDER = principal("x-1", "X", frozenset({Role.ORG_ADMIN}), frozenset({"andere"}), frozenset())
AUDITOR = principal("a-1", "A", frozenset({Role.AUDITOR}), frozenset({"demo"}), frozenset())
OTHER_TEAM = principal(
    "g-2", "G2", frozenset({Role.GROUP_ADMIN}), frozenset(), frozenset({"andere-team"})
)


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


def three_groups(next_id: int = 100) -> InMemoryUnitOfWork:
    """Return ``two_organisations`` with a second group ``demo-twee`` in ``demo``."""
    state = two_organisations(next_id=next_id)
    state.groups[3] = make_group(id=3, organisation_id=1, slug="demo-twee", name="Demo twee")
    return InMemoryUnitOfWork(state)


def audit_rows(uow: InMemoryUnitOfWork) -> list[tuple[str, str, Outcome, str, str | None]]:
    """Return (actor, action, outcome, detail, group) of every audit event, oldest first."""
    return [(e.actor, e.action, e.outcome, e.detail, e.group) for e in uow.state.audit]

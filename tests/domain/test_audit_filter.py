"""The audit filter's subjects (ADR 0067): action prefixes, applied by the fake and by SQL."""

from app.domain.audit import SUBJECT_ACTIONS, AuditEvent, AuditFilter, Outcome, Subject
from tests.support.domain import NOW


def _event(action: str) -> AuditEvent:
    return AuditEvent(NOW, "sub", action, Outcome.SUCCESS)


def test_a_subject_matches_the_actions_that_start_with_its_prefixes() -> None:
    updates = AuditFilter(actions=SUBJECT_ACTIONS[Subject.UPDATES])
    assert updates.matches(_event("system.update"))
    assert updates.matches(_event("system.version"))
    assert not updates.matches(_event("system.maintenance"))
    assert not updates.matches(_event("group.edit"))


def test_without_a_subject_every_action_passes() -> None:
    assert AuditFilter().matches(_event("anything.at_all"))


def test_every_subject_has_prefixes_and_they_do_not_overlap() -> None:
    seen: set[str] = set()
    for subject in Subject:
        prefixes = SUBJECT_ACTIONS[subject]
        assert prefixes
        assert not prefixes & seen
        seen |= prefixes

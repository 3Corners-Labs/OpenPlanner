"""Every event an hourly alert counts is written through its check (ADR 0044).

``app.domain.activity_alert`` stays exact only when each successful event it counts is followed
by the alert's check. ``record_change`` runs it and refuses a counted permission without its
threshold; a static scan of ``app/`` refuses a counted event built by hand.
"""

import ast
from pathlib import Path

import pytest

from app.domain.activity_alert import ALERTS, alert_for
from app.domain.admin import record_change
from app.domain.authorization import Permission
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import GROUP_ADMIN
from tests.support.domain import NOW

APP = Path(__file__).resolve().parents[2] / "app"
COUNTED = {alert.permission for alert in ALERTS}


async def test_a_counted_permission_without_its_threshold_is_refused(
    uow: InMemoryUnitOfWork,
) -> None:
    with pytest.raises(ValueError, match="planner.changes_alert"):
        async with uow.locked() as tx:
            await record_change(tx, NOW, GROUP_ADMIN, Permission.BOOKINGS_MANAGE, "demo", "demo")

    assert uow.state.audit == []


async def test_any_other_permission_needs_no_threshold(uow: InMemoryUnitOfWork) -> None:
    async with uow.locked() as tx:
        await record_change(tx, NOW, GROUP_ADMIN, Permission.GROUP_EDIT, "demo", "demo", "name")

    assert [(e.action, e.detail) for e in uow.state.audit] == [("group.edit", "name")]


def test_each_permission_has_at_most_one_alert() -> None:
    assert len(COUNTED) == len(ALERTS)
    assert [alert_for(a.permission) for a in ALERTS] == list(ALERTS)
    assert alert_for(Permission.GROUP_EDIT) is None


def _counts(action: ast.expr | None) -> bool:
    """Whether ``action`` names a counted permission: ``Permission.X.value`` or its text."""
    if isinstance(action, ast.Constant):
        return action.value in {p.value for p in COUNTED}
    return (
        isinstance(action, ast.Attribute)
        and action.attr == "value"
        and isinstance(action.value, ast.Attribute)
        and isinstance(action.value.value, ast.Name)
        and action.value.value.id == "Permission"
        and action.value.attr in {p.name for p in COUNTED}
    )


def _by_hand(source: str) -> list[int]:
    """Return the lines of ``source`` that build an ``AuditEvent`` with a counted action."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "AuditEvent"):
            continue
        keyword = next((k.value for k in node.keywords if k.arg == "action"), None)
        if _counts(node.args[2] if len(node.args) > 2 else keyword):
            found.append(node.lineno)
    return found


def test_no_counted_event_is_built_by_hand() -> None:
    by_hand = [
        f"{path.relative_to(APP.parent)}:{line}"
        for path in sorted(APP.rglob("*.py"))
        for line in _by_hand(path.read_text())
    ]

    assert by_hand == [], "write these through record_change with the alert's threshold"


def test_the_scan_finds_an_event_built_by_hand() -> None:
    source = (
        "AuditEvent(now, actor, Permission.BOOKINGS_MANAGE.value, Outcome.SUCCESS)\n"
        "AuditEvent(now, actor, action='bookings.view', outcome=Outcome.SUCCESS)\n"
        "AuditEvent(now, actor, Permission.GROUP_EDIT.value, Outcome.SUCCESS)\n"
    )

    assert _by_hand(source) == [1, 2]

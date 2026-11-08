"""Planned changes and their application, shared by the tests of the document writers.

Every ``test_config_*`` module builds ``Change`` objects as the planner hands them to a writer
and applies them in one transaction; the shape of both is the same, only the defaults of a kind
(its key, its organisation, its group) differ, so each test module keeps a two-line wrapper.
"""

from collections.abc import Awaitable, Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db.config_common import Writer
from app.domain.configuration_changes import Change, ChangeAction
from app.domain.configuration_values import Value


def planned(
    kind: str,
    address: tuple[str, ...],
    action: ChangeAction,
    values: Mapping[str, Value] | None = None,
    fields: tuple[str, ...] | None = None,
    *,
    key: tuple[str, ...] | None = None,
    organisation: str | None = None,
    group: str | None = None,
) -> Change:
    """Return a change as the planner hands it to a writer.

    Args:
        kind: The object's kind.
        address: Its natural keys from the top of the tree.
        action: What happens to it.
        values: Every field as it will be; ``fields`` defaults to their names.
        fields: The fields written; ``()`` writes none.
        key: Its key within its parent; the last part of ``address`` by default.
        organisation: The slug of its organisation, if any.
        group: The slug of its group, if any.
    """
    given = dict(values or {})
    return Change(
        kind=kind,
        key=address[-1:] if key is None else key,
        address=address,
        path="x",
        action=action,
        fields=tuple(given) if fields is None else fields,
        values=given,
        organisation=organisation,
        group=group,
    )


async def apply_changes(
    engine: AsyncEngine,
    writers: Mapping[str, Writer],
    box: SecretBox | None,
    *changes: Change,
    after: Callable[[AsyncSession, SecretBox | None], Awaitable[object]] | None = None,
) -> None:
    """Apply ``changes`` with their writers in one committed transaction, then ``after``."""
    async with AsyncSession(engine) as session, session.begin():
        for one in changes:
            await writers[one.kind](session, box, one)
        if after is not None:
            await after(session, box)

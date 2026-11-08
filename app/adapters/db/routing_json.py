"""The stored form of a group's routing and a booking's routing choice (ADR 0081).

``groups.routing`` holds the subjects, switched-on attributes and mapping as one JSON
document; ``bookings.routing_answers`` the attribute codes the participant chose, next to
``bookings.subject_code``; ``schedule_rules.subjects`` and ``schedule_overrides.subjects`` the
subject codes a staffing block offers, comma-separated. None of it is personal data. A stored
entry that no longer validates is skipped rather than failing the page.
"""

import contextlib
import json
from collections.abc import Callable
from typing import Any

import structlog

from app.domain.errors import DomainError
from app.domain.routing import (
    Attribute,
    GroupRouting,
    RouteVariable,
    RoutingChoice,
    Subject,
)

log = structlog.get_logger()


def routing_json(routing: GroupRouting) -> str | None:
    """Return the JSON stored in ``groups.routing``; ``None`` for a group without routing."""
    if routing == GroupRouting():
        return None
    body: dict[str, Any] = {
        "subjects": [{"code": s.code, "labels": dict(s.labels)} for s in routing.subjects],
        "attributes": [a.value for a in routing.attributes],
        "mapping": {
            source: {"name": variable.name, "values": dict(variable.values)}
            for source, variable in routing.mapping
        },
    }
    return json.dumps(body, sort_keys=True)


def routing_from_json(raw: str | None) -> GroupRouting:
    """Return the routing stored in ``groups.routing``; empty for ``None``.

    Read entry by entry: a subject, attribute or mapping that no longer validates (an attribute
    an older release does not know, a rule tightened since) is left out, the rest is kept. The
    column is written only by ``save_group_routing``: a settings save never touches it, but
    the next change on the routing screen saves the routing without the skipped entries. Every
    skip is logged (``routing.entry_skipped``, counts only).
    """
    if not raw:
        return GroupRouting()
    try:
        body = json.loads(raw)
    except ValueError:
        return GroupRouting()
    if not isinstance(body, dict):
        return GroupRouting()
    subjects = _entries(body.get("subjects"), _subject, "subjects")
    attributes = _entries(body.get("attributes"), Attribute, "attributes")
    mapping = body.get("mapping")
    variables = _entries(
        list(mapping.items()) if isinstance(mapping, dict) else [], _variable, "mapping"
    )
    by_code = {s.code: s for s in reversed(subjects)}  # the first of a duplicate code wins
    routing = GroupRouting(
        tuple(s for s in subjects if by_code[s.code] is s), tuple(dict.fromkeys(attributes))
    )
    for source, variable in variables:
        allowed = routing.codes(source)
        values = tuple((code, value) for code, value in variable.values if code in allowed)
        with contextlib.suppress(DomainError):
            routing = routing.with_mapping(source, RouteVariable(variable.name, values))
    return routing


def _entries[T](items: object, read: Callable[[Any], T], part: str) -> tuple[T, ...]:
    """Return ``read(item)`` for every item that reads; broken items are skipped and logged."""
    found: list[T] = []
    skipped = 0
    for item in items if isinstance(items, list) else []:
        try:
            found.append(read(item))
        except (ValueError, KeyError, TypeError, AttributeError, DomainError):
            skipped += 1
    if skipped:
        log.warning("routing.entry_skipped", part=part, count=skipped)
    return tuple(found)


def _subject(item: Any) -> Subject:
    """Read one stored subject."""
    return Subject(item["code"], tuple(sorted(item["labels"].items())))


def _variable(item: Any) -> tuple[str, RouteVariable]:
    """Read one stored mapping entry ``(source, {name, values})``."""
    source, body = item
    return str(source), RouteVariable(body["name"], tuple(sorted(body.get("values", {}).items())))


def choice_json(choice: RoutingChoice | None) -> str | None:
    """Return the JSON stored in ``bookings.routing_answers`` (the attribute codes only)."""
    if choice is None or not choice.attributes:
        return None
    return json.dumps(dict(choice.attributes), sort_keys=True)


def choice_from_columns(subject: str | None, raw: str | None) -> RoutingChoice | None:
    """Return the booking's routing choice from its two columns, or ``None`` without one."""
    attributes: tuple[tuple[str, str], ...] = ()
    if raw:
        try:
            loaded = json.loads(raw)
            attributes = tuple(sorted((str(k), str(v)) for k, v in loaded.items()))
        except (ValueError, AttributeError):
            attributes = ()
    if subject is None and not attributes:
        return None
    return RoutingChoice(subject, attributes)


def subjects_column(subjects: tuple[str, ...]) -> str | None:
    """Return the stored form of a block's subject codes; ``None`` offers every subject."""
    return ",".join(subjects) or None


def subjects_from_column(raw: str | None) -> tuple[str, ...]:
    """Return a block's subject codes from its column."""
    return tuple(sorted({code for code in (raw or "").split(",") if code}))

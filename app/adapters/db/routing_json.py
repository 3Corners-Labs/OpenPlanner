"""The stored form of a group's routing and a booking's routing choice (ADR 0081).

``groups.routing`` holds the subjects, switched-on attributes and mapping as one JSON
document; ``bookings.routing_answers`` the attribute codes the participant chose, next to
``bookings.subject_code``; ``schedule_rules.subjects`` and ``schedule_overrides.subjects`` the
subject codes a staffing block offers, comma-separated. None of it is personal data. A stored
value that no longer validates (a hand-edited row) reads as empty rather than failing the page.
"""

import json
from typing import Any

from app.domain.errors import DomainError
from app.domain.routing import (
    Attribute,
    GroupRouting,
    RouteVariable,
    RoutingChoice,
    Subject,
)


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
    """Return the routing stored in ``groups.routing``; empty for ``None`` or a broken row."""
    if not raw:
        return GroupRouting()
    try:
        body = json.loads(raw)
        subjects = tuple(
            Subject(item["code"], tuple(sorted(item["labels"].items())))
            for item in body.get("subjects", [])
        )
        attributes = tuple(Attribute(value) for value in body.get("attributes", []))
        mapping = tuple(
            (
                source,
                RouteVariable(item["name"], tuple(sorted(item.get("values", {}).items()))),
            )
            for source, item in body.get("mapping", {}).items()
        )
        return GroupRouting(subjects, attributes, mapping)
    except (ValueError, KeyError, TypeError, AttributeError, DomainError):
        return GroupRouting()


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

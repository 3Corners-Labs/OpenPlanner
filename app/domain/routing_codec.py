"""The JSON form of a group's routing, read strictly (ADR 0081, ADR 0099).

``groups.routing`` holds the subjects, the switched-on attributes and the mapping to the
contact center as one JSON object. The database adapter reads it leniently, entry by entry,
because it may have been written by another release. A configuration document is read the way
the portal reads input: nothing is left out, an unknown property, a value of the wrong type or an
attribute the catalogue does not have is refused, and ``GroupRouting`` refuses the rest. The
written form is the stored form, so a document and the database hold the same object.
"""

from collections.abc import Mapping
from typing import Any

from app.domain.errors import DomainError, ErrorCode
from app.domain.routing import Attribute, GroupRouting, RouteVariable, Subject

_KEYS = frozenset({"subjects", "attributes", "mapping"})


def routing_to_json(routing: GroupRouting) -> dict[str, Any] | None:
    """Return the routing as the JSON object that is stored; ``None`` for no routing."""
    if routing == GroupRouting():
        return None
    return {
        "subjects": [{"code": s.code, "labels": dict(s.labels)} for s in routing.subjects],
        "attributes": [a.value for a in routing.attributes],
        "mapping": {
            source: {"name": variable.name, "values": dict(variable.values)}
            for source, variable in routing.mapping
        },
    }


def routing_from_json(data: object) -> GroupRouting:
    """Return the routing a JSON object states, refusing anything the portal would.

    Raises:
        DomainError: ``invalid_configuration`` naming ``routing`` for a shape that is wrong,
            or the part the routing itself refuses (``subject_code``, ``mapping`` …).
    """
    if not isinstance(data, Mapping) or not set(data) <= _KEYS:
        raise _invalid()
    subjects = tuple(_subject(item) for item in _list(data.get("subjects", [])))
    attributes = tuple(_attribute(item) for item in _list(data.get("attributes", [])))
    mapping = data.get("mapping", {})
    if not isinstance(mapping, Mapping):
        raise _invalid()
    variables = tuple((_text(source), _variable(body)) for source, body in mapping.items())
    return GroupRouting(subjects, attributes, variables)


def _invalid() -> DomainError:
    """Return the refusal of a routing object of the wrong shape."""
    return DomainError(ErrorCode.INVALID_CONFIGURATION, "routing")


def _list(value: object) -> list[Any]:
    """Return a JSON array, or refuse."""
    if not isinstance(value, list):
        raise _invalid()
    return value


def _text(value: object) -> str:
    """Return a JSON text, or refuse."""
    if not isinstance(value, str):
        raise _invalid()
    return value


def _texts(value: object) -> dict[str, str]:
    """Return a JSON object of texts, or refuse."""
    if not isinstance(value, Mapping):
        raise _invalid()
    return {_text(key): _text(item) for key, item in value.items()}


def _subject(item: object) -> Subject:
    """Read one subject: its code and its labels per locale."""
    if not isinstance(item, Mapping) or set(item) != {"code", "labels"}:
        raise _invalid()
    return Subject(_text(item["code"]), tuple(sorted(_texts(item["labels"]).items())))


def _attribute(item: object) -> Attribute:
    """Read one switched-on attribute from the closed catalogue."""
    try:
        return Attribute(_text(item))
    except ValueError:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "attributes") from None


def _variable(body: object) -> RouteVariable:
    """Read the variable one source maps to."""
    if not isinstance(body, Mapping) or not {"name"} <= set(body) <= {"name", "values"}:
        raise _invalid()
    values = _texts(body.get("values", {}))
    return RouteVariable(_text(body["name"]), tuple(sorted(values.items())))

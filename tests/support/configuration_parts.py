"""Helpers for testing one slice of the version-1 format on its own (ADR 0099).

A slice (login, channels, scenarios) hangs under an organisation, and the scenarios also under
a group. These helpers build a small format with stub organisations and groups around the
slice's collections, build the current configuration around it, and plan a document whose
sealed secrets have been opened the way the adapter opens them (the token stands for the
plain text, so a test chooses a token that is valid base64).
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.domain.authorization import Permission
from app.domain.configuration_changes import IntegrityRule, RuleContext, Usage
from app.domain.configuration_document import Document
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_schema import Anchor, Collection, Format, Kind, Shape, text
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Sealed, Secret, Value
from tests.support.configuration import allow, entity, parse


def stub_format(
    organisation: tuple[Collection, ...],
    group: tuple[Collection, ...] = (),
    *integrity: IntegrityRule,
) -> Format:
    """Return a format whose organisations (and groups) hold the given collections."""
    collections = list(organisation)
    if group:
        stub = Kind(
            "group",
            key=("slug",),
            permission=Permission.GROUP_EDIT,
            anchor=Anchor.GROUP,
            fields=(text("slug", required=True),),
            collections=group,
        )
        collections.append(Collection("groups", stub, Shape.ENTITIES))
    kind = Kind(
        "organisation",
        key=("slug",),
        permission=Permission.ORGANISATION_MANAGE,
        anchor=Anchor.ORGANISATION,
        fields=(text("slug", required=True),),
        collections=tuple(collections),
    )
    root = Kind("installation", collections=(Collection("organisations", kind, Shape.ENTITIES),))
    return Format("test/v1", root, integrity=integrity)


def organisation(slug: str = "acme", **children: tuple[Entity, ...]) -> Entity:
    """Return a current organisation holding the given collections."""
    return entity("organisation", (slug,), {}, children)


def installation(*organisations: Entity) -> Entity:
    """Return the current configuration around the given organisations."""
    return entity("installation", (), {}, {"organisations": organisations})


def opened(document: Document) -> Document:
    """Return the document with every sealed secret opened (the token is the plain text)."""
    return replace(document, root=_open(document.root))


def _open(node: Entity) -> Entity:
    """Open the secrets of one object and of everything below it."""
    fields: dict[str, Value] = {
        name: Secret(value.token) if isinstance(value, Sealed) else value
        for name, value in node.fields.items()
    }
    children = {
        name: tuple(_open(child) for child in group) for name, group in node.children.items()
    }
    return replace(node, fields=fields, children=children)


def plan_opened(
    data: Mapping[str, Any],
    current: Entity,
    fmt: Format,
    *,
    usage: Usage | None = None,
    context: RuleContext | None = None,
    may_change: Any = allow,
) -> Plan:
    """Plan a document written as a dictionary after opening its secrets."""
    return plan_document(
        opened(parse(data, fmt)),
        current,
        usage=usage or Usage(),
        may_change=may_change,
        context=context or RuleContext(),
        settings_version=1,
    )

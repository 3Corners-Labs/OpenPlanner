"""A small configuration format for the engine's tests (ADR 0099), with real permissions.

The engine (parsing, planning) knows no kind of the real format, so it is tested against this
one: shops hold boxes, a box holds labels, texts, a logo and tags, clients sit at the top and
name a shop. The permissions and their levels are the real ones, so the targets the planner
builds are checked against the matrix's own vocabulary.
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.domain.authorization import Permission, Target
from app.domain.configuration_changes import RuleContext, Usage
from app.domain.configuration_document import Document, parse_document
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_schema import (
    Anchor,
    Collection,
    Format,
    IntegrityRule,
    Kind,
    Shape,
    binary,
    boolean,
    integer,
    json_object,
    objects,
    secret,
    setting,
    strings,
    text,
)
from app.domain.configuration_tree import Entity
from app.domain.configuration_values import Value

ENTRY = Kind(
    "entry",
    key=("locale", "key"),
    permission=Permission.TEXTS_MANAGE,
    tables=frozenset({"entries"}),
    fields=(text("locale", required=True), text("key", required=True), text("text")),
)
LABEL = Kind(
    "label",
    key=("name",),
    tables=frozenset({"labels"}),
    fields=(text("name", required=True, max_length=10), integer("weight", default=1)),
)
LOGO = Kind(
    "logo",
    permission=Permission.BRANDING_MANAGE,
    tables=frozenset({"logos"}),
    fields=(binary("data"),),
)
TAG = Kind(
    "tag",
    key=("tag",),
    tables=frozenset({"tags"}),
    fields=(text("tag", required=True),),
)
BOX = Kind(
    "box",
    key=("slug",),
    permission=Permission.GROUP_EDIT,
    create_permission=Permission.GROUP_CREATE,
    delete_permission=Permission.GROUP_DELETE,
    anchor=Anchor.GROUP,
    tables=frozenset({"boxes"}),
    fields=(
        text("slug", required=True, max_length=20),
        text("name", required=True),
        integer("size", minimum=1, maximum=9, default=5, permission=Permission.SCHEDULE_MANAGE),
        text("colour", choices=("red", "blue"), default="red"),
        text("note", nullable=True),
        json_object("extra", default={}),
        strings("order", ordered=True),
        objects("rules", items=(integer("day", required=True), text("label", nullable=True))),
        secret("password"),
    ),
    collections=(
        Collection("labels", LABEL, Shape.ENTITIES),
        Collection("texts", ENTRY, Shape.MAP),
        Collection("logo", LOGO, Shape.SINGLE),
        Collection("tags", TAG, Shape.KEYS),
    ),
)
SHOP = Kind(
    "shop",
    key=("slug",),
    permission=Permission.ORGANISATION_MANAGE,
    delete_permission=Permission.ORGANISATION_DELETE,
    anchor=Anchor.ORGANISATION,
    tables=frozenset({"shops"}),
    fields=(text("slug", required=True), text("name", required=True)),
    collections=(Collection("boxes", BOX, Shape.ENTITIES),),
)
CLIENT = Kind(
    "client",
    key=("client_id",),
    permission=Permission.API_CLIENTS_MANAGE,
    organisation_field="organisation",
    tables=frozenset({"clients"}),
    fields=(
        text("client_id", required=True),
        text("organisation", required=True),
        boolean("enabled", default=False),
    ),
)
SETTING = Kind(
    "setting",
    key=("key",),
    tables=frozenset({"settings"}),
    fields=(text("key", required=True), setting("value")),
)
SWITCH = Kind(
    "switch",
    tables=frozenset({"switches"}),
    fields=(boolean("enabled", default=False),),
)
PLATFORM = Kind(
    "platform",
    permission=Permission.SETTINGS_PLATFORM_MANAGE,
    deletable=False,
    collections=(
        Collection("settings", SETTING, Shape.MAP),
        Collection("switch", SWITCH, Shape.SINGLE),
    ),
)
ROOT = Kind(
    "installation",
    collections=(
        Collection("platform", PLATFORM, Shape.SINGLE),
        Collection("shops", SHOP, Shape.ENTITIES),
        Collection("clients", CLIENT, Shape.ENTITIES),
    ),
)


def build_format(*integrity: IntegrityRule) -> Format:
    """Return the test format, with the given cross-object rules."""
    return Format("test/v1", ROOT, integrity=integrity)


FORMAT = build_format()


def allow(permission: Permission, target: Target) -> bool:
    """Let everything through: an actor who may do anything."""
    return True


class Recorder:
    """A ``MayChange`` that records what it was asked and denies what it was told to."""

    def __init__(self, *denied: Permission) -> None:
        self.denied = set(denied)
        self.asked: list[tuple[Permission, Target]] = []

    def __call__(self, permission: Permission, target: Target) -> bool:
        self.asked.append((permission, target))
        return permission not in self.denied


def entity(
    kind: str,
    key: tuple[str, ...] = (),
    fields: Mapping[str, Value] | None = None,
    children: Mapping[str, tuple[Entity, ...]] | None = None,
) -> Entity:
    """Return one object of the current configuration."""
    return Entity(kind, key, fields=dict(fields or {}), children=dict(children or {}))


def box(slug: str = "b", **fields: Value) -> Entity:
    """Return a box with the defaults of the format, overridden by ``fields``."""
    values: dict[str, Value] = {
        "name": "Box",
        "size": 5,
        "colour": "red",
        "note": None,
        "extra": {},
        "order": [],
        "rules": [],
    }
    values.update(fields)
    children = values.pop("_children", {})  # type: ignore[arg-type]
    return entity("box", (slug,), values, children)  # type: ignore[arg-type]


def shop(slug: str = "s", *boxes: Entity, name: str = "Shop") -> Entity:
    """Return a shop holding ``boxes``."""
    return entity("shop", (slug,), {"name": name}, {"boxes": tuple(boxes)})


def installation(
    *shops: Entity, clients: tuple[Entity, ...] = (), platform: Entity | None = None
) -> Entity:
    """Return the installation as it is now."""
    children: dict[str, tuple[Entity, ...]] = {"shops": tuple(shops), "clients": clients}
    children["platform"] = (platform or entity("platform"),)
    return entity("installation", (), {}, children)


def parse(data: Mapping[str, Any], fmt: Format = FORMAT) -> Document:
    """Read a document written as a dictionary."""
    return parse_document({"format": "test/v1", **data}, {fmt.name: fmt})


def plan(
    data: Mapping[str, Any],
    current: Entity | None = None,
    *,
    usage: Usage | None = None,
    may_change: Any = allow,
    context: RuleContext | None = None,
    version: int = 1,
    fmt: Format = FORMAT,
) -> Plan:
    """Plan a document written as a dictionary against ``current``."""
    return plan_document(
        parse(data, fmt),
        current if current is not None else installation(shop("s", box("b"))),
        usage=usage or Usage(),
        may_change=may_change,
        context=context or RuleContext(),
        settings_version=version,
    )


def format_with_box(new_box: Kind, *integrity: IntegrityRule) -> Format:
    """Return the test format with its box kind replaced, to give it rules of its own."""
    boxes = replace(SHOP.collection("boxes"), kind=new_box)
    shops = replace(ROOT.collection("shops"), kind=replace(SHOP, collections=(boxes,)))
    root = replace(
        ROOT, collections=(ROOT.collection("platform"), shops, ROOT.collection("clients"))
    )
    return Format("test/v1", root, integrity=integrity)


def plan_with_values(current: Entity, **values: Value) -> Plan:
    """Plan an update of box ``b`` whose fields hold already opened values (secrets, files).

    A document read from JSON holds sealed secrets; the adapter opens them before planning.
    """
    document = parse({"shops": [{"slug": "s", "boxes": [{"slug": "b", "name": "Box"}]}]})
    (shop_doc,) = document.root.children["shops"]
    (box_doc,) = shop_doc.children["boxes"]
    opened = replace(box_doc, fields={**box_doc.fields, **values})
    shops = replace(shop_doc, children={"boxes": (opened,)})
    return plan_document(
        replace(document, root=replace(document.root, children={"shops": (shops,)})),
        current,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(),
        settings_version=1,
    )

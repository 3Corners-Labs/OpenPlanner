"""Trees and documents with skills, pools and flows, for the tests of ADR 0121 step 2b-ii.

``installation()`` is a current configuration of two organisations: skills (one archived), a
counted pool shared by flows of two services, the legacy pools, a flow with published revisions
1 and 2 and a draft 3, a subject with its own skill. ``document_json`` writes it as the document
an export would give, and ``plan`` plans a document against a tree as an actor who may do
everything.
"""

import copy
from collections.abc import Mapping
from typing import Any

from app.domain.configuration_changes import RuleContext, Usage
from app.domain.configuration_document import Document, parse_document, to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS
from app.domain.configuration_values import Value
from tests.support.config_roundtrip import document_of
from tests.support.configuration import allow

PUBLISHED = "2026-11-01T10:00:00+00:00"
LABELS = {"nl": "Balie", "en": "Desk"}
ROUTING = {
    "subjects": [{"code": "paspoort", "labels": {"nl": "Paspoort", "en": "Passport"}}],
    "attributes": [],
    "mapping": {},
}


def entity(
    kind: str,
    key: tuple[str, ...] = (),
    fields: Mapping[str, Value] | None = None,
    **children: tuple[Entity, ...],
) -> Entity:
    """Return one object of a current configuration."""
    return Entity(kind, key, fields=dict(fields or {}), children=dict(children))


def skill(code: str, *, archived: bool = False) -> Entity:
    """Return a skill with labels in both locales."""
    labels = {"nl": code.title(), "en": code.title()}
    return entity("skill", (code,), {"labels": labels, "archived": archived})


def pool(name: str, *skills: str, archived: bool = False) -> Entity:
    """Return a counted pool guaranteeing ``skills``."""
    return entity(
        "capacity_pool",
        (name,),
        {"guaranteed_skills": sorted(skills), "archived": archived},
    )


def legacy_pool(name: str) -> Entity:
    """Return the legacy pool of a group."""
    return entity(
        "legacy_capacity_pool", (), {"name": name, "guaranteed_skills": [], "archived": False}
    )


def revision(number: int, **fields: Value) -> Entity:
    """Return a published, non-legacy telephone revision on the pool ``loket``."""
    values: dict[str, Value] = {
        "labels": LABELS,
        "intake_modes": ["appointment"],
        "interaction_kind": "telephone",
        "duration_minutes": 20,
        "capacity_pool": "loket",
        "legacy_pool_of": None,
        "required_skills": [],
        "subjects": {},
        "dispatch": "own_queue",
        "handling_action": "call",
        "completion": "operator",
        "collects_email": True,
        "legacy": False,
        "published_at": PUBLISHED,
    }
    values.update(fields)
    return entity("flow_revision", (str(number),), values)


def legacy_rev(group: str, mode: str = "appointment", **fields: Value) -> Entity:
    """Return revision 1 of the wrapper flow of ``group``, as the backfill writes it."""
    values: dict[str, Value] = {
        "intake_modes": [mode],
        "interaction_kind": "video",
        "duration_minutes": 30,
        "capacity_pool": None,
        "legacy_pool_of": group,
        "handling_action": "open_video",
        "completion": "legacy_video_observation",
        "legacy": True,
    }
    values.update(fields)
    return revision(1, **values)


def flow(
    code: str,
    state: str,
    current: int | None,
    draft: int | None,
    default_for: list[str],
    *revisions: Entity,
) -> Entity:
    """Return a flow with its revisions."""
    fields: dict[str, Value] = {
        "state": state,
        "default_for": default_for,
        "current_revision": current,
        "draft_revision": draft,
    }
    return entity("service_flow", (code,), fields, revisions=revisions)


def group(slug: str, *, legacy: str | None = None, flows: tuple[Entity, ...] = ()) -> Entity:
    """Return a group of a current configuration with the fields a plan reads."""
    fields: dict[str, Value] = {
        "name": slug.title(),
        "timezone": "Europe/Amsterdam",
        "slot_minutes": 30,
        "routing": ROUTING,
    }
    children: dict[str, tuple[Entity, ...]] = {}
    if legacy is not None:
        children["legacy_pool"] = (legacy_pool(legacy),)
    if flows:
        children["flows"] = flows
    return Entity("group", (slug,), fields=fields, children=children)


def organisation(slug: str, *groups: Entity, **children: tuple[Entity, ...]) -> Entity:
    """Return an organisation holding ``groups`` and the given collections."""
    return Entity(
        "organisation",
        (slug,),
        fields={"name": slug.title()},
        children={**children, "groups": groups},
    )


def installation(*organisations: Entity) -> Entity:
    """Return a current configuration around the organisations."""
    return entity("installation", (), organisations=organisations)


def full() -> Entity:
    """Return the installation of the round-trip tests (see the module docstring)."""
    advies = flow(
        "advies",
        "archived",
        2,
        3,
        [],
        revision(1, required_skills=["passport"]),
        revision(2, required_skills=["passport"], subjects={"paspoort": ["passport"]}),
        revision(3, published_at=None, labels={"nl": "Nieuw", "en": "New"}),
    )
    telefoon = flow("telefoon", "draft", 1, None, [], revision(1))
    acme = organisation(
        "acme",
        group(
            "balie",
            legacy="Balie",
            flows=(
                flow("appointment", "active", 1, None, ["appointment"], legacy_rev("balie")),
                advies,
            ),
        ),
        group("kantoor", legacy="Kantoor", flows=(telefoon,)),
        skills=(skill("passport"), skill("old", archived=True)),
        capacity_pools=(pool("loket", "passport"),),
    )
    beta = organisation("beta", group("vestiging", legacy="Vestiging"), skills=(skill("id-card"),))
    return installation(acme, beta)


def empty_flows() -> Entity:
    """Return the installation of ``full`` before any skill, pool or flow was added."""
    acme = organisation("acme", group("balie"), group("kantoor"))
    return installation(acme, organisation("beta", group("vestiging")))


def document_json(current: Entity | None = None) -> dict[str, Any]:
    """Return the document an export of ``current`` writes, as the JSON a file holds."""
    return copy.deepcopy(to_json(document_of(current or full(), secrets=False)))  # type: ignore[arg-type]


def parse(data: Mapping[str, Any]) -> Document:
    """Read a document written as a dictionary."""
    return parse_document(dict(data), FORMATS)


def plan(
    data: Mapping[str, Any],
    current: Entity,
    *,
    usage: Usage | None = None,
    may_change: Any = allow,
) -> Plan:
    """Plan a document against ``current`` as an actor who may do everything."""
    return plan_document(
        parse(data),
        current,
        usage=usage or Usage(),
        may_change=may_change,
        context=RuleContext(),
        settings_version=1,
    )


def flows_of(data: dict[str, Any], organisation: int = 0, group: int = 0) -> list[dict[str, Any]]:
    """Return the ``flows`` list of a group of an exported document."""
    return data["organisations"][organisation]["groups"][group]["flows"]  # type: ignore[no-any-return]

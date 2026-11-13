"""Trees and documents with skills, pools and flows, for the tests of ADR 0121 step 2b-ii.

``installation()`` is a current configuration of two organisations: skills (one archived), a
counted pool shared by flows of two services, the legacy pools, a flow with published revisions
1 and 2 and a draft 3, a subject with its own skill. ``document_json`` writes it as the document
an export would give, and ``plan`` plans a document against a tree as an actor who may do
everything.
"""

import copy
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.domain.configuration_changes import RuleContext, Usage
from app.domain.configuration_document import Document, parse_document, to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS
from app.domain.configuration_values import Value
from app.domain.contact_center import ProviderDescriptor
from app.domain.errors import ErrorCode
from tests.support.config_roundtrip import document_of
from tests.support.configuration import allow

PUBLISHED = "2026-11-01T10:00:00+00:00"
LATER = "2026-11-02T10:00:00+00:00"


def published(number: int) -> str:
    """Return when revision ``number`` was published: every revision a minute after the last."""
    return f"2026-11-01T10:{number - 1:02d}:00+00:00"


CLOCK = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
"""What the rules take for now: after every time a fixture was published."""
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


def pool(
    code: str,
    *skills: str,
    archived: bool = False,
    name: str | None = None,
    **staffing: Value,
) -> Entity:
    """Return a counted pool with ``code`` guaranteeing ``skills``; its name is the code title.

    ``staffing`` gives ``timezone``, ``staffing_rules`` and ``staffing_overrides`` (ADR 0122).
    """
    return entity(
        "capacity_pool",
        (code,),
        {
            "name": name or code.title(),
            "guaranteed_skills": sorted(skills),
            "archived": archived,
            **staffing,
        },
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
        "published_at": published(number),
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


def full(advies: Entity | None = None) -> Entity:
    """Return the installation of the round-trip tests (see the module docstring).

    ``advies`` replaces the flow ``advies`` (published revisions 1 and 2, draft 3) with another.
    """
    advies = advies or flow(
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
    exported: dict[str, Any] = to_json(document_of(current or full(), secrets=False))
    return copy.deepcopy(exported)


def parse(data: Mapping[str, Any]) -> Document:
    """Read a document written as a dictionary."""
    return parse_document(dict(data), FORMATS)


def plan(
    data: Mapping[str, Any],
    current: Entity,
    *,
    usage: Usage | None = None,
    may_change: Any = allow,
    contact_centers: Mapping[str, ProviderDescriptor] | None = None,
) -> Plan:
    """Plan a document against ``current`` as an actor who may do everything.

    ``contact_centers`` are the platforms the installation has adapters for (none by default).
    """
    return plan_document(
        parse(data),
        current,
        usage=usage or Usage(),
        may_change=may_change,
        context=RuleContext(now=CLOCK, contact_centers=contact_centers or {}),
        settings_version=1,
    )


def flows_of(data: dict[str, Any], organisation: int = 0, group: int = 0) -> list[dict[str, Any]]:
    """Return the ``flows`` list of a group of an exported document."""
    found: list[dict[str, Any]] = data["organisations"][organisation]["groups"][group]["flows"]
    return found


def refusals(result: Any) -> list[tuple[str, ErrorCode, str | None]]:
    """Return what stops a plan as ``(path, code, field)`` triples."""
    return [(r.path, r.code, r.field) for r in result.refusals]


def advies(data: dict[str, Any]) -> dict[str, Any]:
    """Return the flow ``advies`` of the first group of an exported document."""
    return next(f for f in flows_of(data) if f["code"] == "advies")


def draft(data: dict[str, Any]) -> dict[str, Any]:
    """Return the draft revision (number 3) of the flow ``advies``."""
    found: dict[str, Any] = advies(data)["revisions"][2]
    return found


def acme(data: dict[str, Any]) -> dict[str, Any]:
    """Return the organisation ``acme`` of an exported document."""
    found: dict[str, Any] = data["organisations"][0]
    return found


# --- documents written as JSON, for the tests that go through the service --------------------


def encode(data: Mapping[str, Any]) -> bytes:
    """Return a document as the bytes of a file."""
    return json.dumps(data).encode()


def revision_json(number: int = 1, **changes: Any) -> dict[str, Any]:
    """Return a complete telephone revision as a document holds it (a draft unless published)."""
    values: dict[str, Any] = {
        "revision": number,
        "labels": dict(LABELS),
        "intake_modes": ["appointment"],
        "interaction_kind": "telephone",
        "duration_minutes": 20,
        "capacity_pool": None,
        "legacy_pool_of": None,
        "required_skills": [],
        "subjects": {},
        "dispatch": "own_queue",
        "handling_action": "call",
        "completion": "operator",
        "collects_email": True,
        "legacy": False,
        "published_at": None,
    }
    values.update(changes)
    return values


def flow_json(code: str, *revisions: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Return a flow as a document holds it: a draft that points at its last revision."""
    values: dict[str, Any] = {
        "code": code,
        "state": "draft",
        "default_for": [],
        "current_revision": None,
        "draft_revision": revisions[-1]["revision"] if revisions else None,
        "revisions": list(revisions),
    }
    values.update(changes)
    return values


def organisation_document(slug: str, *groups: dict[str, Any], **sections: Any) -> dict[str, Any]:
    """Return a document of one organisation with new services (``groups``) and sections."""
    return {
        "format": "openplanner.config/v1",
        "organisations": [{"slug": slug, "name": slug.title(), **sections, "groups": list(groups)}],
    }


def group_json(slug: str, **sections: Any) -> dict[str, Any]:
    """Return a new service as a document holds it, with the given sections."""
    return {
        "slug": slug,
        "name": slug.title(),
        "timezone": "Europe/Amsterdam",
        "slot_minutes": 30,
        **sections,
    }

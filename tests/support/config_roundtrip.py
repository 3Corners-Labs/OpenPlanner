"""Round trips of a read configuration tree through a document (ADR 0099 step 2).

The property the adapters must keep: what a reader returns, written as a document and planned
against the same tree through the real format, changes nothing and is refused by nothing.
Secrets travel as ``Sealed`` in a document; here the "sealing" is base64 of the plain text, so
a document can be read back and its secrets opened again without the passphrase machinery.
"""

import base64
from collections.abc import Mapping
from dataclasses import replace

from app.domain.configuration_changes import RuleContext, Usage
from app.domain.configuration_document import Document, Mode, Scope, parse_document, to_json
from app.domain.configuration_plan import Plan, plan_document
from app.domain.configuration_tree import Entity
from app.domain.configuration_v1 import FORMATS, V1
from app.domain.configuration_values import Sealed, Secret, Value
from tests.support.configuration import allow


def organisations(**children: Mapping[str, tuple[Entity, ...]]) -> Entity:
    """Return a current installation with the given organisations (slug -> their children)."""
    found = tuple(
        Entity("organisation", (slug,), fields={"name": slug.title()}, children=dict(kids))
        for slug, kids in children.items()
    )
    return Entity(
        "installation",
        (),
        children={"platform": (Entity("platform"),), "organisations": found},
    )


def _map(node: Entity, convert: object) -> Entity:
    """Return ``node`` with ``convert`` applied to every field value, below it too."""
    fields = {name: convert(value) for name, value in node.fields.items()}  # type: ignore[operator]
    children = {
        name: tuple(_map(child, convert) for child in group)
        for name, group in node.children.items()
    }
    return replace(node, fields=fields, children=children)


def _seal(value: Value) -> Value:
    """Seal a secret the way these tests do: base64 of its plain text."""
    if isinstance(value, Secret):
        return Sealed(base64.b64encode(value.reveal().encode()).decode())
    return value


def _open(value: Value) -> Value:
    """Open what ``_seal`` sealed."""
    if isinstance(value, Sealed):
        return Secret(base64.b64decode(value.token).decode())
    return value


def _strip(value: Value) -> Value:
    """Mark a secret for removal."""
    return value


def document_of(current: Entity, *, secrets: bool) -> Document:
    """Write ``current`` as a document, read it back, and open its secrets again.

    Without ``secrets`` the secret fields are left out, as an export without secrets does.
    """
    sealed = _map(current, _seal)
    if not secrets:
        sealed = _drop_sealed(sealed)
    document = Document(V1.name, Mode.MERGE, Scope(), sealed, "unused", V1)
    read = parse_document(to_json(document), FORMATS)
    return replace(read, root=_map(read.root, _open))


def _drop_sealed(node: Entity) -> Entity:
    """Return ``node`` without its sealed fields, below it too."""
    fields = {n: v for n, v in node.fields.items() if not isinstance(v, Sealed)}
    children = {
        name: tuple(_drop_sealed(child) for child in group) for name, group in node.children.items()
    }
    return replace(node, fields=fields, children=children)


def plan_against(document: Document, current: Entity) -> Plan:
    """Plan ``document`` against ``current`` as an actor who may do everything."""
    return plan_document(
        document,
        current,
        usage=Usage(),
        may_change=allow,
        context=RuleContext(),
        settings_version=1,
    )

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
from app.domain.configuration_tree import Entity, drop_fields, map_fields
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


def document_of(current: Entity, *, secrets: bool) -> Document:
    """Write ``current`` as a document, read it back, and open its secrets again.

    Without ``secrets`` the secret fields are left out, as an export without secrets does.
    """
    sealed = map_fields(current, lambda _kind, _at, _name, value: _seal(value))
    if not secrets:
        sealed = drop_fields(sealed, lambda value: isinstance(value, Sealed))
    document = Document(V1.name, Mode.MERGE, Scope(), sealed, "unused", V1)
    read = parse_document(to_json(document), FORMATS)
    return replace(read, root=map_fields(read.root, lambda _kind, _at, _name, value: _open(value)))


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

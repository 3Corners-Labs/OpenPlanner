"""The JSON shape of a stored contact block (ADR 0037 §2), shared by every table that holds one.

Organisation and group blocks, the copy on a closure and a scenario's block are stored the
same way. Reading validates every option again and leaves out what another version wrote.
"""

import json

from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.domain.errors import DomainError


def contacts_json(block: ContactBlock) -> str:
    """Return a contact block as the JSON stored for organisations, groups, closures, scenarios."""
    options = [
        {"kind": o.kind.value, "value": o.value, "labels": dict(o.labels), "hours": dict(o.hours)}
        for o in block.options
    ]
    return json.dumps({"headings": dict(block.headings), "options": options})


def contacts_block(body: str) -> ContactBlock:
    """Return the stored contact block; options another version wrote are left out."""
    data = json.loads(body)
    options = []
    for item in data.get("options", []):
        try:
            options.append(
                ContactOption(
                    ContactKind(item["kind"]),
                    str(item["value"]),
                    dict(item.get("labels", {})),
                    dict(item.get("hours", {})),
                )
            )
        except (KeyError, ValueError, TypeError, DomainError):
            continue  # written by another version; skip rather than fail the page
    return ContactBlock(tuple(options), dict(data.get("headings", {})))

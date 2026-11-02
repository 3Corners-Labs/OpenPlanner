"""User names of staff in the bundled identity provider (ADR 0106 points 5 and 6).

A staff member logs in with ``<local part>@<organisation>.<suffix>``: the suffix is the
platform setting ``bundled_idp_login_suffix`` (default ``openplanner.internal``), the
organisation label is the organisation's ``idp_key`` (ADR 0049, a valid DNS label). Staff
whose first grant reaches everything (the superuser) get ``<local part>@<suffix>``. The name is
personal data: it never goes into a log line, an audit event, a URL or an error message.

    first and last name ──propose_local_part──► jan.de-vries
    grants + places ──organisation_label──► "demo" | None
    local, label, suffix ──user_name──► jan.de-vries@demo.openplanner.internal
    ... taken? ──candidates──► ...-2, ...-3

The format is a fixed rule, not a preference: it keeps names valid as e-mail-like identifiers.
"""

import re
import unicodedata
from collections.abc import Collection, Iterator

from app.domain.authorization import Grant
from app.domain.entitlements import Directory

DEFAULT_SUFFIX = "openplanner.internal"
"""``.internal`` is reserved by ICANN for private use; ``.local`` belongs to multicast DNS."""
LOCAL_PART_MAX = 64
"""The longest local part RFC 5321 allows."""
SUFFIX_MAX = 190
"""Leaves room for a local part and an organisation label within an address of 254."""
SUFFIX_LABELS = (2, 10)
"""A suffix has two to ten labels."""
COLLISION_LIMIT = 99
"""How many numbered alternatives are tried before the invitation is refused."""
FALLBACK_LOCAL_PART = "staff"
"""The proposal for a name that holds no letter or digit the rule allows."""

_LOCAL_PART = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def valid_local_part(value: str) -> bool:
    """Return whether ``value`` is a local part the rule allows (no double dot, no edge dot)."""
    return (
        len(value) <= LOCAL_PART_MAX
        and _LOCAL_PART.fullmatch(value) is not None
        and ".." not in value
    )


def valid_suffix(value: str) -> bool:
    """Return whether ``value`` is a lower-case domain of two to ten DNS labels."""
    labels = value.split(".")
    low, high = SUFFIX_LABELS
    return (
        len(value) <= SUFFIX_MAX
        and low <= len(labels) <= high
        and all(_LABEL.fullmatch(label) for label in labels)
    )


def propose_local_part(first_name: str, last_name: str) -> str:
    """Propose ``first.last``: lower case, diacritics removed, other characters dropped.

    Words are joined by dots, a hyphen inside a word stays, and the result is cut to
    ``LOCAL_PART_MAX`` characters on a clean edge.
    """
    plain = unicodedata.normalize("NFKD", f"{first_name} {last_name}")
    ascii_only = "".join(c for c in plain if not unicodedata.combining(c)).lower()
    words = (re.sub(r"[^a-z0-9-]", "", word).strip("-") for word in ascii_only.split())
    local = ".".join(word for word in words if word)[:LOCAL_PART_MAX].strip(".-")
    return local or FALLBACK_LOCAL_PART


def organisation_label(grants: Collection[Grant], places: Directory) -> str | None:
    """Return the ``idp_key`` of the first grant's organisation, or ``None`` for a global one.

    An organisation admin's grants lie inside their own organisations (ADR 0095 point 3), so
    their invitations always carry one of those labels (ADR 0106 point 8).
    """
    for grant in grants:
        if grant.organisation is None:
            return None
        found = places.organisation_by_slug(grant.organisation)
        if found is not None:
            return found.idp_key
    return None


def user_name(local_part: str, label: str | None, suffix: str) -> str:
    """Join the parts: ``local@label.suffix``, or ``local@suffix`` without a label."""
    return f"{local_part}@{label}.{suffix}" if label else f"{local_part}@{suffix}"


def candidates(local_part: str, label: str | None, suffix: str) -> Iterator[str]:
    """Yield the name, then ``-2`` up to ``COLLISION_LIMIT``, each within ``LOCAL_PART_MAX``."""
    yield user_name(local_part, label, suffix)
    for number in range(2, COLLISION_LIMIT + 1):
        tail = f"-{number}"
        base = local_part[: LOCAL_PART_MAX - len(tail)].rstrip(".-")
        yield user_name(f"{base}{tail}", label, suffix)

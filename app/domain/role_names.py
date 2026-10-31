"""The names an organisation gives its organisation admin and group admin (ADR 0077).

Like the agent term (``app.domain.agent_term``) a name is a label: ``app.domain.authorization``
reads no word, the audit log and the exports keep the role code, and the rights matrix document
keeps the shipped names. Only the roles whose function differs per organisation can be named:
the organisation admin and the group admin. The superuser (a platform role), the auditor (whose
name states a privacy guarantee, ADR 0039 B7) and custom roles (which have names of their own)
cannot.
"""

from collections.abc import Collection

from app.domain.agent_term import MAX_TERM_LENGTH, MIN_TERM_LENGTH
from app.domain.errors import DomainError, ErrorCode
from app.domain.message_texts import check_text

ROLE_NAME_KEYS = ("authz.role.org_admin", "authz.role.group_admin")
"""The catalogue keys of the roles an organisation may name; the same keys hold its own names."""


def clean_role_name(key: str, text: str, taken: Collection[str] = ()) -> str:
    """Return the trimmed name ``text`` for the role ``key`` if the organisation may store it.

    Rules: one of ``ROLE_NAME_KEYS``; at least ``MIN_TERM_LENGTH`` and at most
    ``MAX_TERM_LENGTH`` characters, one line, no control characters and no braces; and not
    equal, ignoring case, to a name in ``taken`` (the shipped names of the other roles, the
    other name of the same form, the agent term), so nobody can put "Organisatiebeheerder" on
    the group admin and mislead a reader about who may do what.

    Raises:
        DomainError: ``invalid_configuration`` with field ``key``.
    """
    if key not in ROLE_NAME_KEYS:
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, "key")
    cleaned = check_text(key, text, frozenset(), MAX_TERM_LENGTH, single_line=True)
    if (
        len(cleaned) < MIN_TERM_LENGTH
        or "{" in cleaned
        or "}" in cleaned
        or cleaned.casefold() in {name.casefold() for name in taken}
    ):
        raise DomainError(ErrorCode.INVALID_CONFIGURATION, key)
    return cleaned

"""Who an admin is across identity providers: provider-qualified subjects (ADR 0083 point 16).

OIDC promises that ``sub`` is unique within one issuer only, so two providers may hand out the
same ``sub`` for two people. The platform provider of the environment keeps the bare ``sub``,
so existing staff records, audit history and ``created_by`` values keep their meaning. An
organisation provider's subject is stored as ``idp:<provider key>:<sub>``; a platform ``sub``
that already starts with ``idp:`` is refused, so the platform can never pose as an
organisation's admin. The provider of a stored subject follows from the subject itself:

    qualify("", "user-1")        ──► "user-1"
    qualify("gem-a", "1234")     ──► "idp:gem-a:1234"     provider_of ──► "gem-a"
    qualify("", "idp:gem-a:1")   ──► SubjectRefusedError("reserved_subject")
"""

import re

from app.domain.entitlements import IDP_KEY_PATTERN

PLATFORM = ""
"""Key of the platform provider configured in the environment (ADR 0083 point 1)."""

QUALIFIED_PREFIX = "idp:"
"""Start of every organisation provider's subject; reserved for them."""

MAX_SUBJECT_LENGTH = 255
"""Room for a subject in ``staff.subject`` and the audit ``actor`` columns."""

RESERVED_SUBJECT = "reserved_subject"
SUBJECT_TOO_LONG = "subject_too_long"
EMPTY_SUBJECT = "empty_subject"

_PROVIDER_KEY = re.compile(IDP_KEY_PATTERN)


class SubjectRefusedError(Exception):
    """A ``sub`` that cannot become an identity; ``reason`` is the refusal code.

    Args:
        reason: ``reserved_subject``, ``subject_too_long`` or ``empty_subject``.
    """

    def __init__(self, reason: str) -> None:
        """Store the reason; it is also the message (never the ``sub`` itself)."""
        super().__init__(reason)
        self.reason = reason


def qualify(provider: str, sub: str) -> str:
    """Return the identity of ``sub`` at ``provider``: bare for the platform, else qualified.

    Raises:
        SubjectRefusedError: ``empty_subject``, ``reserved_subject`` (a platform ``sub``
            starting with ``idp:``) or ``subject_too_long``.
        ValueError: ``provider`` is not a valid provider key (a programming error).
    """
    if provider != PLATFORM and _PROVIDER_KEY.fullmatch(provider) is None:
        raise ValueError("provider key")
    if not sub.strip():
        raise SubjectRefusedError(EMPTY_SUBJECT)
    if provider == PLATFORM:
        if sub.startswith(QUALIFIED_PREFIX):
            raise SubjectRefusedError(RESERVED_SUBJECT)
        subject = sub
    else:
        subject = f"{QUALIFIED_PREFIX}{provider}:{sub}"
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise SubjectRefusedError(SUBJECT_TOO_LONG)
    return subject


def provider_of(subject: str) -> str:
    """Return the key of the provider a stored subject came from (``PLATFORM`` if bare)."""
    if not subject.startswith(QUALIFIED_PREFIX):
        return PLATFORM
    return subject[len(QUALIFIED_PREFIX) :].split(":", 1)[0]

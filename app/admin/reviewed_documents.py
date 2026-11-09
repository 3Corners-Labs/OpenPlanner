"""The configuration document an admin is reviewing, kept until they apply it (ADR 0099 point 13).

The portal shows a plan, then applies it. Between the two the document has to be the same one,
so the upload is kept here, keyed by a random token, and the apply form carries only the token
and the plan hash. What is kept is the document as uploaded: its secrets are still sealed under
the passphrase, which is asked again at the apply and never stored, unless the author wrote them
in clear text (``{"plain": ...}``, ADR 0099 point 8a), which then sit in this process's memory for
the review time. An entry belongs to one
admin and one scope, lives ``configuration_review_minutes`` and is replaced by the admin's next
upload for that scope, so the store holds at most one document per admin and scope. It is
memory of this process only: after a restart the admin uploads again, which costs a minute and
keeps a document on no disk.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.configuration_document import Scope

TOKEN_BYTES = 24
"""Random bytes in a review token (192 bits, unguessable)."""


@dataclass(frozen=True)
class Reviewed:
    """A document that waits for its apply.

    Attributes:
        token: The random token the apply form carries.
        data: The document as uploaded.
        plan_hash: The hash of the plan the admin was shown.
        expires: When the entry lapses.
    """

    token: str
    data: bytes
    plan_hash: str
    expires: datetime


class ReviewedDocuments:
    """Keeps the document each admin is reviewing, one per admin and scope."""

    def __init__(self) -> None:
        """Start empty."""
        self._by_owner: dict[tuple[str, str | None], Reviewed] = {}

    def keep(
        self,
        subject: str,
        scope: Scope,
        data: bytes,
        plan_hash: str,
        now: datetime,
        minutes: int,
    ) -> Reviewed:
        """Keep ``data`` for ``subject`` in ``scope``, replacing their earlier one."""
        self._drop_lapsed(now)
        entry = Reviewed(
            secrets.token_urlsafe(TOKEN_BYTES), data, plan_hash, now + timedelta(minutes=minutes)
        )
        self._by_owner[(subject, scope.organisation)] = entry
        return entry

    def find(self, subject: str, scope: Scope, token: str, now: datetime) -> Reviewed | None:
        """Return the entry of ``subject`` in ``scope`` when ``token`` is its token and it lives."""
        self._drop_lapsed(now)
        entry = self._by_owner.get((subject, scope.organisation))
        if entry is None or not secrets.compare_digest(entry.token, token):
            return None
        return entry

    def discard(self, subject: str, scope: Scope) -> None:
        """Forget the entry of ``subject`` in ``scope`` (after an apply, or a cancel)."""
        self._by_owner.pop((subject, scope.organisation), None)

    def _drop_lapsed(self, now: datetime) -> None:
        """Remove the entries whose time has passed."""
        for owner in [o for o, e in self._by_owner.items() if e.expires <= now]:
            del self._by_owner[owner]

"""Audit events: who did what to which organisation or group, and with what outcome.

Written for every login, every denied authorisation decision and every change (ADR 0014).
An event never contains personal data of citizens; the actor is the subject identifier from
the identity provider. The admin's display name lives once in ``AdminProfile`` and is joined
in when the log is read, so events stay immutable and a name is stored in one place only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Outcome(StrEnum):
    """Result of the audited action."""

    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


@dataclass(frozen=True)
class AuditEvent:
    """One audited action.

    Attributes:
        at: When it happened (UTC).
        actor: Subject identifier of the admin, or ``anonymous``.
        action: A permission (``group.view``) or an event key (``admin.login``).
        outcome: Success, denied or failure.
        reason: Machine-readable reason (an authorisation ``Reason`` or an error code).
        organisation: Slug of the organisation concerned, if any.
        group: Slug of the group concerned, if any.
        detail: What changed, as machine keys (changed field names, ``rule.add``); never
            values, so no personal data.
    """

    at: datetime
    actor: str
    action: str
    outcome: Outcome
    reason: str = ""
    organisation: str | None = None
    group: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class AuditEntry:
    """An audit event as the audit log shows it.

    Attributes:
        event: The stored event.
        actor_name: The actor's latest known display name; ``None`` if unknown.
    """

    event: AuditEvent
    actor_name: str | None = None


@dataclass(frozen=True)
class AdminProfile:
    """What the portal remembers about an admin, refreshed at every login.

    Attributes:
        subject: Subject identifier from the identity provider.
        display_name: Name from the identity provider, for the audit log.
        last_login_at: Time of the latest login (UTC).
    """

    subject: str
    display_name: str = field(repr=False)
    last_login_at: datetime

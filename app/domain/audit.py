"""Audit events: who did what to which organisation or group, and with what outcome.

Written for every login, every denied authorisation decision and every change (ADR 0014).
An event never contains personal data of citizens; the actor is the subject identifier from
the identity provider.
"""

from dataclasses import dataclass
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
    """

    at: datetime
    actor: str
    action: str
    outcome: Outcome
    reason: str = ""
    organisation: str | None = None
    group: str | None = None

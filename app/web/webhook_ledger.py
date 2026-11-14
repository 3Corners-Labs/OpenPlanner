"""The web side of the delivery ledger: tell it what the gate decided, write its alert (ADR 0124).

``app.domain.webhook_ledger`` is pure; this module is where a request reaches it, and where its
alert is written. It owns the unit of work for the alert the way ``app.web.security_events`` owns
it for the security events (the domain's ``activity_alert`` writes inside the transaction of the
action it counts, which a refused webhook does not have). The application's ledger and the writer
of its alert are on ``app.state`` (``install_webhook_ledger``, called by ``app.main``):

    gate ─► ``note_refused`` (every refusal, in ``_refuse``) ─┐
    gate ─► ``note_delivered`` (the pause)                     ├─► ledger ─► alert? ─► system event
    route ─► ``note_outcome`` (what came of a verified event) ─┘
    card, status screens ─► ``ledger_view`` ─► what this process has seen since the window began

Telling the ledger or writing its alert never changes a webhook's answer: a failure is logged by
its class only and swallowed, like the security event's. When the alert could not be written the
ledger takes its mark back (``retract``), so the next counted outcome raises it again. Nothing
from the request is passed on.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import structlog
from fastapi import FastAPI, Request

from app.config import Settings
from app.domain.audit import SYSTEM_ACTOR, AuditEvent, Outcome, Source
from app.domain.inbound_gate import Refusal
from app.domain.integration import EventOutcome
from app.domain.ports import Clock, UnitOfWork
from app.domain.webhook_ledger import (
    AddressKey,
    Alert,
    Delivered,
    LedgerView,
    WebhookLedger,
)
from app.domain.webhook_sources import ChainFault

log = structlog.get_logger()

ALERT_ACTION = "webhook.refusal_alert"
"""The system event an address whose group passed the threshold raises, once per group and window
(declared once in ``app.domain.log_events``)."""

ALERT_REASON = "too_many_refusals"
"""Reason of ``webhook.refusal_alert``."""

AlertWriter = Callable[[Alert], Awaitable[None]]
"""Writes the alert's system event (bound to the unit of work and the clock)."""

_DELIVERED = {
    EventOutcome.UNKNOWN: Delivered.UNKNOWN_WORK,
    EventOutcome.DUPLICATE: Delivered.DUPLICATE,
}
"""What a verified event came to, when it was not simply accepted."""


def install_webhook_ledger(
    app: FastAPI, uow: UnitOfWork, clock: Clock, live: Callable[[], Settings]
) -> None:
    """Put the application's delivery ledger and the writer of its alert on ``app.state``.

    The window and the cap are the security log's (``security_event_window_minutes``,
    ``security_event_max_sources``) and the threshold is ``webhook_refusal_alert_count``, all read
    from the live settings at each call, so a change in the portal applies without a restart.

    Args:
        app: The application.
        uow: Opens the transaction the alert is written in.
        clock: Current time.
        live: Returns the settings as they are now.
    """

    def dropped(platform: str) -> None:
        log.warning("webhook.ledger_full", source=platform)

    async def write(alert: Alert) -> None:
        await write_alert(uow, alert, clock.now())

    app.state.webhook_ledger = WebhookLedger(
        clock,
        lambda: timedelta(minutes=live().security_event_window_minutes),
        lambda: live().webhook_refusal_alert_count,
        max_entries=lambda: live().security_event_max_sources,
        on_dropped=dropped,
    )
    app.state.webhook_alert_writer = write


async def write_alert(uow: UnitOfWork, alert: Alert, at: datetime) -> None:
    """Write the alert as one system event for the organisation.

    The detail carries the platform's code, the group, the count and the reasons' codes; nothing
    from any call. An address of the installation names no organisation, so only the superuser
    sees it.

    Args:
        uow: Opens the database transaction.
        alert: What the ledger said.
        at: When it happened.
    """
    detail = (
        f"platform:{alert.key.platform},group:{alert.group.value},count:{alert.count},"
        f"reasons:{'+'.join(alert.reasons)}"
    )
    async with uow.locked() as tx:
        await tx.add_audit_event(
            AuditEvent(
                at,
                SYSTEM_ACTOR,
                ALERT_ACTION,
                Outcome.FAILURE,
                ALERT_REASON,
                alert.key.organisation,
                None,
                detail,
                Source.SYSTEM,
            )
        )


def ledger_view(request: Request, key: AddressKey) -> LedgerView | None:
    """Return what this process's ledger knows of ``key``, or ``None`` (none, or no ledger)."""
    ledger: WebhookLedger | None = getattr(request.app.state, "webhook_ledger", None)
    return ledger.view(key) if ledger is not None else None


async def _tell(request: Request, told: Callable[[WebhookLedger], Alert | None]) -> None:
    """Tell the application's ledger something and write the alert it returns, if any.

    A failure of the ledger or of the write is logged by its class and swallowed; a write that
    failed takes the alert's mark back so a later outcome raises it again.
    """
    state = request.app.state
    ledger: WebhookLedger | None = getattr(state, "webhook_ledger", None)
    if ledger is None:
        return
    try:
        alert = told(ledger)
    except Exception as exc:  # a bug here must not turn a webhook into a 500
        log.warning("webhook.ledger_failed", error=type(exc).__name__)
        return
    writer: AlertWriter | None = getattr(state, "webhook_alert_writer", None)
    if alert is None or writer is None:
        return
    try:
        await writer(alert)
    except asyncio.CancelledError:
        ledger.retract(alert)  # a cancelled request must not leave the mark set
        raise
    except Exception as exc:  # the webhook's answer does not depend on its alert
        ledger.retract(alert)
        log.warning("webhook.alert_failed", error=type(exc).__name__)


async def note_refused(
    request: Request,
    key: AddressKey,
    reason: Refusal,
    *,
    inside_list: bool = False,
    fault: ChainFault | None = None,
) -> None:
    """Tell the ledger the gate refused a call, and write the alert if that one is the one."""
    await _tell(
        request, lambda ledger: ledger.refused(key, reason, inside_list=inside_list, fault=fault)
    )


async def note_delivered(request: Request, key: AddressKey, outcome: Delivered) -> None:
    """Tell the ledger a call passed every check, and write the alert if that one is the one."""
    await _tell(request, lambda ledger: ledger.delivered(key, outcome))


async def note_outcome(request: Request, key: AddressKey, outcome: EventOutcome) -> None:
    """Tell the ledger what a verified event came to: accepted, a duplicate or unknown work."""
    await note_delivered(request, key, _DELIVERED.get(outcome, Delivered.ACCEPTED))

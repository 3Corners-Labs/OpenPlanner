"""The web side of the delivery ledger: tell it what the gate decided, write its alert (ADR 0124).

``app.domain.webhook_ledger`` is pure; this module is where a request reaches it. The application's
ledger and the writer of its alert are on ``app.state`` (``app.main``):

    gate ─► ``note_refused`` (every refusal, in ``_refuse``) ─┐
    gate ─► ``note_delivered`` (the pause)                     ├─► ledger ─► alert? ─► system event
    route ─► ``note_outcome`` (what came of a verified event) ─┘
    card, status screens ─► ``ledger_view`` ─► what this process has seen since the window began

Telling the ledger or writing its alert never changes a webhook's answer: a failure is logged by
its class only and swallowed, like the security event's. Nothing from the request is passed on.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta

import structlog
from fastapi import Request

from app.config import Settings
from app.domain.inbound_gate import Refusal
from app.domain.integration import EventOutcome
from app.domain.ports import Clock, UnitOfWork
from app.domain.webhook_ledger import (
    AddressKey,
    Alert,
    Delivered,
    LedgerView,
    WebhookLedger,
    write_alert,
)
from app.domain.webhook_sources import ChainFault

log = structlog.get_logger()

AlertWriter = Callable[[Alert], Awaitable[None]]
"""Writes the alert's system event (the composition root binds the unit of work and the clock)."""

_DELIVERED = {
    EventOutcome.UNKNOWN: Delivered.UNKNOWN_WORK,
    EventOutcome.DUPLICATE: Delivered.DUPLICATE,
}
"""What a verified event came to, when it was not simply accepted."""


def webhook_ledger(
    uow: UnitOfWork, clock: Clock, live: Callable[[], Settings]
) -> tuple[WebhookLedger, AlertWriter]:
    """Return the application's delivery ledger and the writer of its alert (composition root).

    The window and the cap are the security log's (``security_event_window_minutes``,
    ``security_event_max_sources``) and the threshold is ``webhook_refusal_alert_count``, all read
    from the live settings at each call, so a change in the portal applies without a restart.

    Args:
        uow: Opens the transaction the alert is written in.
        clock: Current time.
        live: Returns the settings as they are now.
    """
    ledger = WebhookLedger(
        clock,
        lambda: timedelta(minutes=live().security_event_window_minutes),
        lambda: live().webhook_refusal_alert_count,
        max_entries=lambda: live().security_event_max_sources,
    )

    async def write(alert: Alert) -> None:
        await write_alert(uow, alert, clock.now())

    return ledger, write


def ledger_view(request: Request, key: AddressKey) -> LedgerView | None:
    """Return what this process's ledger knows of ``key``, or ``None`` (none, or no ledger)."""
    ledger: WebhookLedger | None = getattr(request.app.state, "webhook_ledger", None)
    return ledger.view(key) if ledger is not None else None


async def _raise(request: Request, alert: Alert | None) -> None:
    """Write the alert the ledger returned, if any; a failure is logged and swallowed."""
    if alert is None:
        return
    writer: AlertWriter | None = getattr(request.app.state, "webhook_alert_writer", None)
    if writer is None:
        return
    try:
        await writer(alert)
    except Exception as exc:  # the webhook's answer does not depend on its alert
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
    ledger: WebhookLedger | None = getattr(request.app.state, "webhook_ledger", None)
    if ledger is None:
        return
    try:
        alert = ledger.refused(key, reason, inside_list=inside_list, fault=fault)
    except Exception as exc:  # a bug here must not turn a refusal into a 500
        log.warning("webhook.ledger_failed", error=type(exc).__name__)
        return
    await _raise(request, alert)


async def note_delivered(request: Request, key: AddressKey, outcome: Delivered) -> None:
    """Tell the ledger a call passed every check, and write the alert if that one is the one."""
    ledger: WebhookLedger | None = getattr(request.app.state, "webhook_ledger", None)
    if ledger is None:
        return
    try:
        alert = ledger.delivered(key, outcome)
    except Exception as exc:
        log.warning("webhook.ledger_failed", error=type(exc).__name__)
        return
    await _raise(request, alert)


async def note_outcome(request: Request, key: AddressKey, outcome: EventOutcome) -> None:
    """Tell the ledger what a verified event came to: accepted, a duplicate or unknown work."""
    await note_delivered(request, key, _DELIVERED.get(outcome, Delivered.ACCEPTED))

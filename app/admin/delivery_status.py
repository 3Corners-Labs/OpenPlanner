"""What the delivery ledger says about an address, as a page shows it (ADR 0124 points 10 and 13).

The connection card and System status say the same thing about the same ledger, in the same
words, so the words are made once here from ``app.domain.webhook_ledger``:

    LedgerView ─► ``delivery_view`` ─► last accepted, last refused with its reason in words,
                                        calls per outcome in the window, faults of single calls
                                        by kind, events dropped while paused

Every code is shown through a catalogue text (``app/i18n``), never raw. What the page shows is
what this web process has seen since the window began: the ledger lives in memory (a restart
empties it, each process has its own), and the page says so. Nothing here holds an address of a
caller, a body or a signature: the ledger holds codes, counts and times only.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from app.adapters.contact_centers import INBOUND
from app.domain.authorization import Principal
from app.domain.connection_health import Platform
from app.domain.inbound_gate import Refusal
from app.domain.webhook_ledger import AddressKey, Delivered, LedgerView
from app.domain.webhook_sources import ChainFault
from app.i18n import BoundGettext
from app.web.webhook_ledger import ledger_view

OUTCOME_LABELS: dict[str, str] = {
    Delivered.ACCEPTED.value: "admin.delivery.outcome.accepted",
    Delivered.DUPLICATE.value: "admin.delivery.outcome.duplicate",
    Delivered.PAUSED.value: "admin.delivery.outcome.paused",
    Delivered.UNKNOWN_WORK.value: "admin.delivery.outcome.unknown_work",
    Refusal.UNKNOWN_ADDRESS.value: "admin.delivery.reason.unknown_address",
    Refusal.NETWORK.value: "admin.delivery.reason.network",
    Refusal.SOURCE_UNKNOWN.value: "admin.delivery.reason.source_unknown",
    Refusal.RATE.value: "admin.delivery.reason.rate",
    Refusal.CONTENT_TYPE.value: "admin.delivery.reason.content_type",
    Refusal.TOO_LARGE.value: "admin.delivery.reason.too_large",
    Refusal.SIGNATURE.value: "admin.delivery.reason.invalid_signature",
    Refusal.STALE.value: "admin.delivery.reason.stale",
    Refusal.MALFORMED.value: "admin.delivery.reason.malformed",
}
"""The words of every outcome and refusal reason, in the order the table shows them."""

FAULT_LABELS: dict[ChainFault, str] = {
    ChainFault.WIDE_TRUST: "admin.delivery.fault.wide_trust",
    ChainFault.UNREADABLE_TRUST: "admin.delivery.fault.unreadable_trust",
    ChainFault.UNTRUSTED_HOP: "admin.delivery.fault.untrusted_hop",
    ChainFault.CALLER_IS_PROXY: "admin.delivery.fault.caller_is_proxy",
    ChainFault.HOP_NOT_HANDED_ON: "admin.delivery.fault.hop_not_handed_on",
    ChainFault.NO_ADDRESS: "admin.delivery.fault.no_address",
}
"""The words of every per-call fault of the proxy chain."""


@dataclass(frozen=True)
class DeliveryRow:
    """One line of a count table: what, and how many calls."""

    label: str
    count: int


@dataclass(frozen=True)
class DeliveryView:
    """One address's deliveries as the page shows them.

    Attributes:
        last_accepted: When a delivery was last accepted, or the words for *none seen*.
        last_refused: When a call was last refused and why, or the words for *none seen*.
        window: When the counts began ("since 09:00"), or empty without calls in the window.
        outcomes: Calls per outcome in the window, non-zero rows only, in a fixed order.
        faults: Faults of single calls by kind in the window, non-zero rows only.
        inside_list: Refusals in the window from inside the connection's own list of networks.
        paused_dropped: Verified events dropped while the address was paused, in the window.
    """

    last_accepted: str
    last_refused: str
    window: str
    outcomes: list[DeliveryRow]
    faults: list[DeliveryRow]
    inside_list: int
    paused_dropped: int


def delivery_view(
    _: BoundGettext, view: LedgerView | None, moment: Callable[[datetime], str]
) -> DeliveryView:
    """Return the page's words for an address's ledger entry.

    Args:
        _: The request's translator.
        view: What the ledger knows (``None``: it never saw the address in this process).
        moment: Writes a time in the reader's zone and language.
    """
    if view is None:
        view = LedgerView(None, {}, {}, 0, 0, None, None, None)
    never = _("admin.delivery.none_seen")
    refused = never
    if view.last_refused_at is not None and view.last_refused_reason is not None:
        refused = _(
            "admin.delivery.last_refused_at",
            time=moment(view.last_refused_at),
            reason=_(OUTCOME_LABELS[view.last_refused_reason]),
        )
    return DeliveryView(
        moment(view.last_accepted) if view.last_accepted else never,
        refused,
        _("admin.delivery.window_since", time=moment(view.window_started))
        if view.window_started
        else "",
        [
            DeliveryRow(_(label), view.outcomes[code])
            for code, label in OUTCOME_LABELS.items()
            if view.outcomes.get(code)
        ],
        [
            DeliveryRow(_(label), view.faults[fault])
            for fault, label in FAULT_LABELS.items()
            if view.faults.get(fault)
        ],
        view.inside_list,
        view.paused_dropped,
    )


async def paused_addresses(
    request: Request, principal: Principal
) -> dict[tuple[Platform, str], int]:
    """Return the paused webhook addresses, keyed by platform and organisation's slug.

    For the status screens (ADR 0124 point 9): each says paused instead of showing a healthy or a
    silent connection, looking up its own platform. The value is the events this web process
    dropped at the address in the window (its ledger). Only organisations the reader may manage
    integrations for.
    """
    service = request.app.state.contact_center_connection_service
    found: dict[tuple[Platform, str], int] = {}
    for slug, kind in (await service.paused_connections(principal)).items():
        inbound = INBOUND[kind]
        view = ledger_view(request, AddressKey(inbound.source, slug))
        found[(inbound.platform, slug)] = view.paused_dropped if view else 0
    return found

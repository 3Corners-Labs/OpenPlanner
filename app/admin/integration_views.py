"""What the integrations page shows of the last webhook checks (read-only).

``webhooks-list``, ``webhooks-reset`` and ``webhooks-health`` store their results
(``ReportStore``); this module turns them into rows with translated labels. The catalogue keys
are written out literally so the catalogue test can check them. Cleaning up stays on the
command line (runbook, section 6): the page offers no destructive action.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.adapters.db.reports import StoredReport
from app.adapters.registrations import Reason, State, listing_from_report
from app.adapters.webhook_selftest import Outcome, deliveries_from_report

STATE_LABELS: dict[State, tuple[str, str]] = {
    State.CURRENT: ("admin.integrations.state.current", "success"),
    State.STALE: ("admin.integrations.state.stale", "warning"),
    State.FOREIGN: ("admin.integrations.state.foreign", ""),
}
"""Label key and chip tone per state."""

REASON_LABELS: dict[Reason, str] = {
    Reason.NONE: "",
    Reason.FOREIGN: "admin.integrations.reason.foreign",
    Reason.OTHER_TARGET: "admin.integrations.reason.other_target",
    Reason.UNFILTERED: "admin.integrations.reason.unfiltered",
    Reason.UNKNOWN_HOST: "admin.integrations.reason.unknown_host",
    Reason.UNUSED_EVENT: "admin.integrations.reason.unused_event",
    Reason.INACTIVE: "admin.integrations.reason.inactive",
    Reason.DUPLICATE: "admin.integrations.reason.duplicate",
}

SECRET_LABELS: dict[bool | None, str] = {
    True: "admin.integrations.secret.yes",
    False: "admin.integrations.secret.no",
    None: "admin.integrations.secret.unknown",
}

OUTCOME_LABELS: dict[Outcome, str] = {
    Outcome.DELIVERED: "admin.integrations.outcome.delivered",
    Outcome.REFUSED: "admin.integrations.outcome.refused",
    Outcome.SIGNATURE_REFUSED: "admin.integrations.outcome.signature_refused",
    Outcome.UNSIGNED_ACCEPTED: "admin.integrations.outcome.unsigned_accepted",
    Outcome.NOT_FOUND: "admin.integrations.outcome.not_found",
    Outcome.RATE_LIMITED: "admin.integrations.outcome.rate_limited",
    Outcome.PROXY_ERROR: "admin.integrations.outcome.proxy_error",
    Outcome.UNREACHABLE: "admin.integrations.outcome.unreachable",
    Outcome.UNEXPECTED: "admin.integrations.outcome.unexpected",
}


@dataclass(frozen=True)
class RegistrationRow:
    """One registration as the page shows it."""

    platform: str
    label: str
    target: str
    filter: str
    status: str
    secret: str
    state: str
    tone: str
    reason: str


@dataclass(frozen=True)
class DeliveryRow:
    """One test delivery as the page shows it."""

    endpoint: str
    kind: str
    status: str
    outcome: str
    ok: bool


@dataclass(frozen=True)
class CheckView:
    """The last listing and delivery test; ``None`` times mean no check ran yet."""

    listed_at: str | None
    listed_base: str
    registrations: list[RegistrationRow]
    missing: list[str]
    failed: list[str]
    tested_at: str | None
    tested_base: str
    deliveries: list[DeliveryRow]


def check_view(
    listed: StoredReport | None,
    tested: StoredReport | None,
    _: Callable[..., str],
    moment: Callable[[datetime], str],
) -> CheckView:
    """Build the view from the stored reports with ``_`` (gettext) and ``moment`` (dates)."""
    listing = listing_from_report(listed.body) if listed else None
    deliveries = deliveries_from_report(tested.body) if tested else []
    rows = [
        RegistrationRow(
            platform=r.platform,
            label=r.label,
            target=r.target,
            filter=r.filter,
            status=r.status,
            secret=_(SECRET_LABELS[r.secret]),
            state=_(STATE_LABELS[r.state][0]),
            tone=STATE_LABELS[r.state][1],
            reason=_(REASON_LABELS[r.reason]) if REASON_LABELS[r.reason] else "",
        )
        for r in (listing.registrations if listing else [])
    ]
    return CheckView(
        listed_at=moment(listed.checked_at) if listed else None,
        listed_base=str(listed.body.get("base", "")) if listed else "",
        registrations=rows,
        missing=listing.missing if listing else [],
        failed=[
            _("admin.integrations.listing_failed", platform=e.platform, code=e.code)
            for e in (listing.errors if listing else [])
        ],
        tested_at=moment(tested.checked_at) if tested else None,
        tested_base=str(tested.body.get("base", "")) if tested else "",
        deliveries=[
            DeliveryRow(
                endpoint=f"/webhooks/{d.endpoint}",
                kind=_(
                    "admin.integrations.delivery.signed"
                    if d.signed
                    else "admin.integrations.delivery.unsigned"
                ),
                status=str(d.status or "–"),
                outcome=_(OUTCOME_LABELS[d.outcome]),
                ok=d.ok,
            )
            for d in deliveries
        ],
    )

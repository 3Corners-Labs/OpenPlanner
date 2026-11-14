"""The organisation's own contact-center connection on its *Kanalen* page (ADR 0032 points 9–10).

The card's form, its view (the webhook address and when the secret was made) and the two
actions without a form: rotating the webhook secret and removing the connection. Saving the form
re-renders the whole page with its errors, so that route lives with the page in
``channel_routes``. Every route depends on ``integrations.manage`` for the organisation, which
ADR 0039 keeps for the superuser; the secrets are write-only and never rendered.
"""

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.adapters.contact_centers import DESCRIPTORS, INBOUND
from app.admin import forms
from app.admin.authz import AdminContext
from app.admin.channel_fields import Manage, channels_url, secret_field, value_of
from app.admin.delivery_status import delivery_view
from app.admin.forms import Field, FormData, moment_label
from app.admin.pages import Locale, SameOrigin
from app.admin.source_findings import card_finding
from app.domain.authorization import Principal
from app.domain.contact_center import ContactCenterKind, OrganisationConnection, Registration
from app.domain.contact_center_connection import ContactCenterConnectionService
from app.domain.errors import DomainError
from app.domain.networks import NetworkList
from app.domain.source_networks import MAX_WEBHOOK_NETWORKS
from app.domain.webhook_ledger import AddressKey
from app.domain.webhook_sources import source_list_that_applies
from app.i18n import BoundGettext
from app.web.locale import RequestLocale
from app.web.protection import RejectedRequest
from app.web.webhook_ledger import ledger_view
from app.web.webhook_sources import contact_center_networks, proxy_chain

router = APIRouter(prefix="/admin", include_in_schema=False)

CONNECTION_ANCHOR = "contact-center"
"""The id of the contact-center card, so a confirmation scrolls to it."""

SAVED_DONE = {
    Registration.REGISTERED: "contact_center_registered",
    Registration.NOT_READY: "contact_center_saved_not_ready",
    Registration.UNTOUCHED: "contact_center_saved",
    Registration.REFUSED: "contact_center_not_registered",
    Registration.FAILED: "contact_center_not_registered",
}
"""The confirmation after saving, by what registering the address came to (CC-4d)."""
INBOUND_STATES = {"on": True, "off": False}
"""The state a button of the pause form asks for, by its value."""
ROTATED_DONE = {
    Registration.REGISTERED: "webhook_secret_rotated",
    Registration.NOT_READY: "webhook_secret_rotated",
    Registration.REFUSED: "contact_center_not_registered",
    Registration.FAILED: "contact_center_not_registered",
}
"""The confirmation after a new secret, by what registering it came to."""


def _back(org: str, done: str) -> Response:
    """Return to the card on the organisation's *Kanalen* page with confirmation ``done``."""
    return forms.redirect_done(channels_url(org), done, anchor=CONNECTION_ANCHOR)


def connection_service(request: Request) -> ContactCenterConnectionService:
    """Return the application's contact-center connection service (ADR 0032 point 9)."""
    service: ContactCenterConnectionService = request.app.state.contact_center_connection_service
    return service


def _connection_fields(
    form: FormData, stored: OrganisationConnection | None, _: BoundGettext
) -> list[Field]:
    """Return the contact-center connection inputs; the secrets are write-only."""
    secret_hint = _("admin.channels.secret_hint_set")
    return [
        Field(
            "cc_kind",
            _("admin.channels.cc_kind"),
            "select",
            form.values.get("cc_kind", stored.kind.value if stored else ContactCenterKind.WEBEX_CC),
            error=form.error_for("cc_kind"),
            options=tuple(
                forms.Option(kind.value, _(d.label_key)) for kind, d in DESCRIPTORS.items()
            ),
        ),
        Field(
            "cc_api_base_url",
            _("admin.channels.cc_api_base_url"),
            "url",
            value_of(form, "cc_api_base_url", stored.api_base_url if stored else None),
            hint=_("admin.channels.cc_api_base_url_hint"),
            error=form.error_for("cc_api_base_url"),
            attributes={"autocapitalize": "none", "spellcheck": "false"},
        ),
        Field(
            "cc_client_id",
            _("admin.channels.cc_client_id"),
            value=value_of(form, "cc_client_id", stored.client_id if stored else None),
            hint=_("admin.channels.cc_client_id_hint"),
            error=form.error_for("cc_client_id"),
            attributes={"autocapitalize": "none", "spellcheck": "false"},
        ),
        Field(
            "cc_allowed_networks",
            _("admin.channels.cc_allowed_networks"),
            "textarea",
            value_of(
                form,
                "cc_allowed_networks",
                "\n".join(stored.allowed_networks.networks) if stored else "",
            ),
            hint=_("admin.channels.cc_allowed_networks_hint", maximum=MAX_WEBHOOK_NETWORKS),
            error=form.error_for("cc_allowed_networks"),
            required=False,
            attributes={"rows": "4", "autocapitalize": "none", "spellcheck": "false"},
        ),
        secret_field(
            "cc_client_secret",
            _("admin.channels.cc_client_secret"),
            secret_hint
            if stored and stored.has_client_secret
            else _("admin.channels.cc_client_secret_hint"),
            form,
            required=not (stored and stored.has_client_secret),
        ),
        secret_field(
            "cc_refresh_token",
            _("admin.channels.cc_refresh_token"),
            secret_hint
            if stored and stored.has_refresh_token
            else _("admin.channels.cc_refresh_token_hint"),
            form,
            required=False,
        ),
    ]


async def connection_view(
    request: Request,
    locale: RequestLocale,
    context: AdminContext,
    org: str,
    form: FormData,
    tz: ZoneInfo,
) -> dict[str, object]:
    """Return the contact-center card (ADR 0032, ADR 0124 point 13).

    It holds the form, the webhook address, when its secret was made, where its webhooks may
    come from, whether the address is paused (the chip and the sentence, ADR 0124 point 9) and
    what the gate has seen at the address (``delivery``: this web process's ledger, point 10).
    """
    service = connection_service(request)
    _ = locale.gettext
    stored = await service.view(context.principal, org)
    settings = request.app.state.settings
    base = settings.webhook_base
    made = stored.webhook_secret_at if stored else None
    own = stored.allowed_networks if stored else NetworkList()
    sources = card_finding(
        _,
        source_list_that_applies(own, contact_center_networks(request)),
        own=own.configured,
        required=settings.webhook_source_networks_required,
        chain=proxy_chain(request),
    )
    return {
        "available": service.available,
        "stored": stored is not None,
        "fields": _connection_fields(form, stored, _),
        "webhook": INBOUND[stored.kind].organisation_url(base, org) if stored else "",
        "secret_made": moment_label(made, tz, locale.code) if made else "",
        "sources": sources,
        "paused": stored is not None and not stored.inbound_enabled,
        "delivery": delivery_view(
            _,
            ledger_view(request, AddressKey(INBOUND[stored.kind].source, org)),
            lambda at: moment_label(at, tz, locale.code),
        )
        if stored
        else None,
    }


async def paused_addresses(request: Request, principal: Principal) -> dict[str, int]:
    """Return the organisations whose webhook address is paused, with the events dropped for each.

    For the status screens (ADR 0124 point 9): *Koppelingen* and the overview's status widget say
    paused instead of showing a healthy or a silent connection. The count is this web process's
    ledger (the window's), summed over the platforms an organisation could be connected to.
    """
    paused = await connection_service(request).paused_organisations(principal)
    return {
        slug: sum(
            view.paused_dropped
            for inbound in INBOUND.values()
            if (view := ledger_view(request, AddressKey(inbound.source, slug))) is not None
        )
        for slug in paused
    }


@router.post("/organisations/{org}/channels/contact-center/webhook-secret")
async def rotate_contact_center_secret(
    request: Request, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Generate a new webhook secret for the organisation's connection."""
    try:
        outcome = await connection_service(request).rotate_webhook_secret(context.principal, org)
    except DomainError as error:
        forms.raise_refusal(error)
        return _back(org, "contact_center_refused")
    return _back(org, ROTATED_DONE[outcome])


@router.post("/organisations/{org}/channels/contact-center/inbound")
async def set_contact_center_inbound(
    request: Request, locale: Locale, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Pause or resume the organisation's webhook address (ADR 0124 point 9).

    The button's value says which state is wanted (``off`` or ``on``), so a stale page that
    presses *Pause* twice pauses twice and never toggles. Nothing is registered at the platform.
    """
    wanted = (await forms.read_form(request, locale)).values.get("inbound")
    if wanted not in INBOUND_STATES:
        raise RejectedRequest(422)
    enabled = INBOUND_STATES[wanted]
    try:
        await connection_service(request).set_inbound(context.principal, org, enabled=enabled)
    except DomainError as error:
        forms.raise_refusal(error)
        return _back(org, "contact_center_refused")
    return _back(org, "contact_center_resumed" if enabled else "contact_center_paused")


@router.post("/organisations/{org}/channels/contact-center/delete")
async def remove_contact_center(
    request: Request, org: str, _: SameOrigin, context: Manage
) -> Response:
    """Remove the organisation's connection; it falls back to the installation's."""
    try:
        await connection_service(request).remove(context.principal, org)
    except DomainError as error:
        forms.raise_refusal(error)
        return _back(org, "contact_center_refused")
    return _back(org, "removed")

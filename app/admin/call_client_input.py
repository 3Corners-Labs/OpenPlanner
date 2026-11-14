"""The group settings form's choice of client and what it offers next to the call (ADR 0123 6a).

Next to ``app.admin.task_kind_input``: ``with_call_client_fields`` puts the select for
``join_client`` and, for an embedded group, the select for ``embedded_call_webex_option`` into the
integration inputs, and ``read_call_client`` reads what the form chose, refusing ``embedded`` where
the rules of ``app.domain.call_client`` do:

    - it is offered only while ``embedded_call_enabled`` is on; a group that already holds it keeps
      it on the list (saving another setting never resets it) and is told that participants get
      the redirect while the switch is off;
    - choosing it afresh is refused until the latest service-app check minted a guest token. The
      form asks the stored report of that check (``guest_check_passed``) and calls nobody.

The option's select is shown only for an embedded group, because it acts only there; a form without
it keeps what the group has.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import Request

from app.adapters.db.reports import ReportStore
from app.admin.forms import Field, FormData, Option
from app.domain.call_client import EmbeddedBlocker, embedded_blocker
from app.domain.call_page import OtherWayOffer
from app.domain.guest_access import guest_check_passed
from app.domain.integration_check import SERVICE_APP_REPORT
from app.domain.planning import GroupSettings
from app.domain.scheduling import JoinClient
from app.i18n import BoundGettext

CLIENT_FIELD = "join_client"
OPTION_FIELD = "embedded_call_webex_option"
AFTER_FIELD = "trial_calls_per_booking"
"""The input the two selects follow: the trial call's last setting, where ``join_client`` sat."""

CLIENT_LABELS: dict[JoinClient, str] = {
    JoinClient.BROWSER: "admin.group.join_client_browser",
    JoinClient.CHOICE: "admin.group.join_client_choice",
    JoinClient.EMBEDDED: "admin.group.join_client_embedded",
}
"""The label of each client; every client has one (a test checks)."""
OPTION_LABELS: dict[OtherWayOffer, str] = {
    OtherWayOffer.HIDDEN: "admin.group.webex_option_hidden",
    OtherWayOffer.ON_FAILURE: "admin.group.webex_option_on_failure",
    OtherWayOffer.ALWAYS: "admin.group.webex_option_always",
}
"""The label of each value of the option."""
BLOCKER_MESSAGES: dict[EmbeddedBlocker, str] = {
    EmbeddedBlocker.PLATFORM_SWITCH: "admin.form.error.embedded_platform_switch",
    EmbeddedBlocker.GUEST_CHECK: "admin.form.error.embedded_guest_check",
}
"""The plain sentence of each reason ``embedded`` is refused."""


@dataclass(frozen=True)
class EmbeddedOffer:
    """What this installation allows a service to choose (ADR 0123 points 17 and 18).

    Attributes:
        enabled: ``embedded_call_enabled``, the platform switch.
        guest_check_passed: Whether the latest service-app check minted a guest token.
    """

    enabled: bool
    guest_check_passed: bool

    @property
    def blocker(self) -> EmbeddedBlocker | None:
        """Why ``embedded`` cannot be chosen now, ``None`` when it can."""
        return embedded_blocker(
            platform_enabled=self.enabled, guest_check_passed=self.guest_check_passed
        )


async def embedded_offer(request: Request) -> EmbeddedOffer:
    """Read the platform switch and the stored result of the guest check for the form."""
    report = await ReportStore(request.app.state.engine).get(SERVICE_APP_REPORT)
    return EmbeddedOffer(
        request.app.state.settings.embedded_call_enabled,
        guest_check_passed(report.body if report else None),
    )


def organisation_option(request: Request, organisation_id: int) -> str:
    """Return the organisation's own value of the option, which a service without one follows."""
    live = getattr(request.app.state, "live_settings", None)
    settings = live.for_organisation(organisation_id) if live else request.app.state.settings
    return str(settings.embedded_call_webex_option)


def _shown(form: FormData, current: GroupSettings, offer: EmbeddedOffer) -> JoinClient:
    """Return the client the form shows: the submitted one when it is offered, else the group's."""
    try:
        submitted = JoinClient(form.values.get(CLIENT_FIELD, current.join_client.value))
    except ValueError:
        return current.join_client
    return submitted if submitted in _offered(current.join_client, offer) else current.join_client


def _offered(current: JoinClient, offer: EmbeddedOffer) -> tuple[JoinClient, ...]:
    """Return the clients the select offers: ``embedded`` only while the switch is on or held."""
    return tuple(
        client
        for client in JoinClient
        if client is not JoinClient.EMBEDDED or offer.enabled or current is client
    )


def with_call_client_fields(
    fields: Sequence[Field],
    form: FormData,
    current: GroupSettings,
    offer: EmbeddedOffer,
    organisation_default: str,
    _: BoundGettext,
) -> list[Field]:
    """Return ``fields`` with the client's select, and the option's for an embedded group.

    Both follow the trial call's last input. The hint of the client's select says what a held
    ``embedded`` does while the switch is off (participants get the redirect) and, while the
    switch is on and the check has not passed, why choosing it will be refused.
    """
    hint = _("admin.field.join_client_hint")
    if current.join_client is JoinClient.EMBEDDED and not offer.enabled:
        hint = f"{hint} {_('admin.field.join_client_embedded_off')}"
    elif offer.enabled and not offer.guest_check_passed:
        hint = f"{hint} {_('admin.field.join_client_embedded_unchecked')}"
    shown = _shown(form, current, offer)
    extra = [
        Field(
            CLIENT_FIELD,
            _("admin.field.join_client"),
            "select",
            shown.value,
            hint=hint,
            error=form.error_for(CLIENT_FIELD),
            options=tuple(
                Option(c.value, _(CLIENT_LABELS[c])) for c in _offered(current.join_client, offer)
            ),
        )
    ]
    if shown is JoinClient.EMBEDDED:
        held = current.embedded_call_webex_option
        extra.append(
            Field(
                OPTION_FIELD,
                _("admin.field.embedded_call_webex_option"),
                "select",
                form.values.get(OPTION_FIELD) or (held.value if held else organisation_default),
                hint=_("admin.field.embedded_call_webex_option_hint"),
                error=form.error_for(OPTION_FIELD),
                options=tuple(Option(o.value, _(OPTION_LABELS[o])) for o in OtherWayOffer),
            )
        )
    after = next((n for n, f in enumerate(fields) if f.name == AFTER_FIELD), len(fields) - 1) + 1
    return [*fields[:after], *extra, *fields[after:]]


def read_call_client(
    form: FormData, current: GroupSettings, offer: EmbeddedOffer
) -> tuple[JoinClient | None, OtherWayOffer | None]:
    """Return the client and the option the form chose; a refused choice is a field error.

    ``embedded`` is accepted when the group already holds it, or when the switch is on and the
    guest check passed; otherwise the select gets a plain sentence and nothing is saved. A form
    without the option's select keeps the group's value.
    """
    allowed = {client.value for client in _offered(current.join_client, offer)}
    chosen = form.choice(CLIENT_FIELD, allowed)
    client = JoinClient(chosen) if chosen else None
    if client is JoinClient.EMBEDDED and current.join_client is not JoinClient.EMBEDDED:
        blocker = offer.blocker
        if blocker is not None:
            form.reject(CLIENT_FIELD, BLOCKER_MESSAGES[blocker])
            client = None
    option = current.embedded_call_webex_option
    if OPTION_FIELD in form.values:
        picked = form.choice(OPTION_FIELD, {o.value for o in OtherWayOffer})
        option = OtherWayOffer(picked) if picked else None
    return client, option

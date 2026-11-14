"""The group settings form's choice of client and what it offers next to the call (ADR 0123 6a).

Next to ``app.admin.task_kind_input``: ``with_call_client_fields`` puts the select for
``join_client`` and the select for ``embedded_call_webex_option`` into the integration inputs, and
``read_call_client`` reads what the form chose, refusing ``embedded`` where the rules of
``app.domain.call_client`` do:

    - it is offered only while ``embedded_call_enabled`` is on; a group that already holds it keeps
      it on the list (saving another setting never resets it) and is told that participants get
      the redirect while the switch is off;
    - choosing it afresh is refused until the latest service-app check minted a guest token.

Nothing is looked up here: the view that renders or saves the form resolves the platform switch,
the finding of the guest check (``EmbeddedOffer``) and the organisation's value of the option, and
hands them in.

The option's select is shown wherever ``embedded`` is offered or held. Its first choice, "as the
organisation", stores nothing (a service without its own value follows the organisation's), so a
save that did not touch it never pins the organisation's current value; a form without the select
keeps what the group has.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.admin.forms import Field, FormData, Option
from app.domain.call_client import EmbeddedBlocker, embedded_blocker
from app.domain.call_page import OtherWayOffer
from app.domain.planning import GroupSettings
from app.domain.scheduling import JoinClient
from app.i18n import BoundGettext

CLIENT_FIELD = "join_client"
OPTION_FIELD = "embedded_call_webex_option"
AFTER_FIELD = "trial_calls_per_booking"
"""The input the two selects follow: the trial call's last setting, where ``join_client`` sat."""

FOLLOW_ORGANISATION = ""
"""The value of the option's first choice: no value of its own, the organisation's holds."""

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
"""The plain sentence of each reason ``embedded`` is refused. The platform switch's is kept for
completeness: the form does not offer ``embedded`` while the switch is off, so only the
configuration package reaches that reason (``EmbeddedBlocker.PLATFORM_SWITCH``)."""


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
    organisation_option: OtherWayOffer,
    _: BoundGettext,
) -> list[Field]:
    """Return ``fields`` with the client's select and the option's, after the trial call's input.

    The hint of the client's select says what a held ``embedded`` does while the switch is off
    (participants get the redirect) and, while the switch is on and the check has not passed, why
    choosing it will be refused, or, for a group that holds it, what to do about it. The option's
    select is there whenever ``embedded`` is offered or held; its first choice names
    ``organisation_option``, the value that holds for a service without its own.
    """
    held = current.join_client is JoinClient.EMBEDDED
    hint = _("admin.field.join_client_hint")
    if held and not offer.enabled:
        hint = f"{hint} {_('admin.field.join_client_embedded_off')}"
    elif offer.enabled and not offer.guest_check_passed:
        note = (
            _("admin.field.join_client_embedded_held_unchecked")
            if held
            else _("admin.field.join_client_embedded_unchecked")
        )
        hint = f"{hint} {note}"
    extra = [
        Field(
            CLIENT_FIELD,
            _("admin.field.join_client"),
            "select",
            _shown(form, current, offer).value,
            hint=hint,
            error=form.error_for(CLIENT_FIELD),
            options=tuple(
                Option(c.value, _(CLIENT_LABELS[c])) for c in _offered(current.join_client, offer)
            ),
        )
    ]
    if offer.enabled or held:
        own = current.embedded_call_webex_option
        value = (
            form.values[OPTION_FIELD]
            if OPTION_FIELD in form.values
            else (own.value if own else FOLLOW_ORGANISATION)
        )
        follow = _("admin.group.webex_option_follow", value=_(OPTION_LABELS[organisation_option]))
        extra.append(
            Field(
                OPTION_FIELD,
                _("admin.field.embedded_call_webex_option"),
                "select",
                value,
                hint=_("admin.field.embedded_call_webex_option_hint"),
                error=form.error_for(OPTION_FIELD),
                options=(
                    Option(FOLLOW_ORGANISATION, follow),
                    *(Option(o.value, _(OPTION_LABELS[o])) for o in OtherWayOffer),
                ),
            )
        )
    after = next((n for n, f in enumerate(fields) if f.name == AFTER_FIELD), len(fields) - 1) + 1
    return [*fields[:after], *extra, *fields[after:]]


def read_call_client(
    form: FormData, current: GroupSettings, offer: EmbeddedOffer
) -> tuple[JoinClient | None, OtherWayOffer | None]:
    """Return the client and the option the form chose; a refused choice is a field error.

    ``embedded`` is accepted when the group already holds it, or when the switch is on and the
    guest check passed; otherwise the select gets a plain sentence and nothing is saved. The
    option is ``None`` for "as the organisation" (and while the form refused, when nothing is
    saved anyway); a form without the option's select keeps the group's value.
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
        picked = (
            None
            if form.values[OPTION_FIELD] == FOLLOW_ORGANISATION
            else form.choice(OPTION_FIELD, {o.value for o in OtherWayOffer})
        )
        option = OtherWayOffer(picked) if picked else None
    return client, option

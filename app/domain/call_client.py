"""Which client a participant of a booking gets into the call (ADR 0123 points 1, 11 and 17).

A service (the group, ADR 0121 point 1) chooses how its participants join: the video platform's
browser page (``browser``), the platform's own choice page (``choice``) or the call page of our
own (``embedded``). A flow may override its service; the field on the flow revision is built with
the first video flow (ADR 0123 step 12), so the flow's value is an argument that is ``None`` today.
Both are read here and nowhere else (a test scans ``app/`` for it), so no route, job or template
decides it for itself:

    chosen_client(service, flow)                    what was chosen, whatever the switch says
    call_client(service, flow, platform_enabled)    which client acts for the booking
    uses_call_page(service, flow, platform_enabled) whether the booking's participant gets our page
    redirect_client(service, flow)                  how a redirect to the platform is shaped
    other_way_offer(service, default)               what the page shows next to the call
    allows_other_way(offer)                         whether the platform's address may answer
    embedded_blocker(...)                           why ``embedded`` cannot be chosen now

Pure Python: the platform switch ``embedded_call_enabled`` (class C) and the finding of the guest
check come in as arguments read at the edge.
"""

from enum import StrEnum

from app.domain.call_page import OtherWayOffer
from app.domain.calls import RedirectClient
from app.domain.scheduling import Group, JoinClient

DEFAULT_JOIN_CLIENT = JoinClient.BROWSER
"""The client of a service that has not chosen one (ADR 0075), and what a stored ``embedded``
acts as while the platform switch is off: the participant gets the redirect they got before."""


def chosen_client(service: Group, flow: JoinClient | None = None) -> JoinClient:
    """Return the client chosen for a booking of ``service``, whatever the platform switch says.

    The flow's own value when it has one, else the service's. For what a participant really gets
    ask ``call_client``; this one is for a text that is true for both outcomes (the message's
    line about how the call opens).
    """
    return flow if flow is not None else service.join_client


def call_client(
    service: Group, flow: JoinClient | None = None, *, platform_enabled: bool
) -> JoinClient:
    """Return the client a participant of a booking of ``service`` gets.

    ``embedded`` acts only while the platform switch is on; while it is off a stored ``embedded``
    acts as the default client, so nobody is sent to a page that does not exist.

    Args:
        service: The booking's group.
        flow: The ``join_client`` of the booking's flow revision, ``None`` for "as the service"
            (every flow today).
        platform_enabled: Whether ``embedded_call_enabled`` is on now.
    """
    chosen = chosen_client(service, flow)
    if chosen is JoinClient.EMBEDDED and not platform_enabled:
        return DEFAULT_JOIN_CLIENT
    return chosen


def uses_call_page(
    service: Group, flow: JoinClient | None = None, *, platform_enabled: bool
) -> bool:
    """Return whether the participant of a booking of ``service`` gets the call page.

    The one question every route of the call page asks. The platform switch is the kill switch:
    ``call_client`` already answers the default client for every service while it is off.
    """
    return call_client(service, flow, platform_enabled=platform_enabled) is JoinClient.EMBEDDED


def redirect_client(service: Group, flow: JoinClient | None = None) -> RedirectClient:
    """Return how a redirect to the video platform is shaped for a booking of ``service``.

    The one place that turns a client into the redirect's shape, for ``/join/go`` and the trial
    call alike. ``embedded`` has no page of the platform's of its own: its way around our page
    (the option of point 11, a trial call until it uses our page) is the platform's web app
    straight away, as for the default, so no app is offered that the organisation did not choose.
    Whether the switch is on does not matter: the answer for ``embedded`` is the same either way.
    """
    if chosen_client(service, flow) is JoinClient.CHOICE:
        return RedirectClient.CHOICE
    return RedirectClient.BROWSER


def other_way_offer(service: Group, default: str) -> OtherWayOffer:
    """Return what the call page offers next to the call (ADR 0123 point 11).

    The service's own value, else ``default``, the organisation's setting: the organisation's
    value is the default of every service and a service may set its own (ADR 0042 classes C and D
    keep the organisation's; the service's is stored empty until it chooses).
    """
    return service.embedded_call_webex_option or OtherWayOffer.of(default)


def allows_other_way(offer: OtherWayOffer) -> bool:
    """Return whether the address that leads to the platform may answer for an embedded service.

    ``hidden`` never does: the page shows no way and the address leads back to the page. The
    other two values show the link (before joining, or after a failure, which the server cannot
    tell), so the address must work.
    """
    return offer is not OtherWayOffer.HIDDEN


class EmbeddedBlocker(StrEnum):
    """Why a service cannot be saved as ``embedded`` now (ADR 0123 points 17 and 18)."""

    PLATFORM_SWITCH = "platform_switch"
    """``embedded_call_enabled`` is off on this installation. The settings form never offers
    ``embedded`` while the switch is off, so a crafted post gets the form's generic choice error;
    this reason is reached through the configuration package, which names the field."""
    GUEST_CHECK = "guest_check"
    """The service-app check has not minted a guest token for this installation."""


def embedded_blocker(*, platform_enabled: bool, guest_check_passed: bool) -> EmbeddedBlocker | None:
    """Return what stops a service from being saved as ``embedded``, ``None`` when nothing does.

    Applies to choosing ``embedded``, never to keeping it: a service that already holds it can
    still change its other settings.
    """
    if not platform_enabled:
        return EmbeddedBlocker.PLATFORM_SWITCH
    if not guest_check_passed:
        return EmbeddedBlocker.GUEST_CHECK
    return None

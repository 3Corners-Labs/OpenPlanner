"""What the portal says about an address's webhooks: where they may come from, and a pause.

(ADR 0124 points 3, 4, 9, 13.)

The organisation's connection card and System status say the same thing about the state, in the
same words, so the judgement is made once here from the pure rule (``app.domain.webhook_sources``):

    list that applies, host's switch ─► ``SourceState``  ─► not restricted / restricted /
                                                           required and missing / unreadable
    proxy chain                       ─► ``ChainFault``s ─► what to repair, while a list applies

A pause (point 9) is a finding too, built here for the card: ``pause_finding``. A finding is
never a silent state: an address that any source may call says so, a list that cannot
be read says that every call is refused, and a list the gate cannot evaluate (the chain cannot be
trusted) says that its calls are refused. The detail of a fault names the host's configuration and
belongs to System status only; the organisation's card says that deliveries are refused because
of the installation's proxy setting and whom to ask, without naming the variable. The faults of
one call (the caller is the proxy, a hop did not hand it on, no address, headers its peer was not
trusted to send) are decided per call and show in the events, not here. Texts are in
``app/i18n``; nothing here holds an address of a caller.
"""

from dataclasses import dataclass, replace

from app.domain.networks import NetworkList
from app.domain.webhook_sources import ChainFault, ProxyChain, SourceState, source_state
from app.i18n import BoundGettext

STATE_CHIPS = {
    SourceState.UNRESTRICTED: "warning",
    SourceState.RESTRICTED: "success",
    SourceState.REQUIRED_MISSING: "danger",
    SourceState.UNREADABLE: "danger",
}
STATE_TEXTS = {
    SourceState.UNRESTRICTED: "admin.source.state.unrestricted",
    SourceState.REQUIRED_MISSING: "admin.source.state.required_missing",
    SourceState.UNREADABLE: "admin.source.state.unreadable",
}
FAULT_TEXTS = {
    ChainFault.WIDE_TRUST: "admin.source.fault.wide_trust",
    ChainFault.UNREADABLE_TRUST: "admin.source.fault.unreadable_trust",
}
"""The faults that hold for every call, with the detail for System status. The card does not use
them: it says one fixed line when there are any (``admin.source.fault.card``)."""


@dataclass(frozen=True)
class SourceFinding:
    """One address's source networks as the page shows them.

    Attributes:
        state: Which of the states the address is in.
        text: The state in words (not restricted, restricted to N networks, list missing or
            unreadable).
        chip: The tone of the chip: ``success``, ``warning`` or ``danger``.
        faults: Why the gate would refuse a call for this list (standing faults of the proxy
            chain); empty without a list or with a trustworthy chain. The page words them.
    """

    state: SourceState
    text: str
    chip: str
    faults: tuple[ChainFault, ...]


def source_finding(
    _: BoundGettext,
    sources: NetworkList,
    *,
    own: bool,
    required: bool,
    chain: ProxyChain,
) -> SourceFinding:
    """Return the finding for an address governed by ``sources``, in System status's words.

    Args:
        _: The request's translator.
        sources: The list that applies (a connection's own, else the default); empty: none.
        own: Whether the list is the connection's own rather than the installation's default.
        required: ``WEBHOOK_SOURCE_NETWORKS_REQUIRED``.
        chain: What is known about the proxy chain.
    """
    state = source_state(sources, required=required)
    faults: tuple[ChainFault, ...] = ()
    if state is SourceState.RESTRICTED:
        key = (
            "admin.source.state.restricted_own" if own else "admin.source.state.restricted_default"
        )
        text = _(key, count=len(sources.networks))
        faults = chain.standing_faults()
    elif state is SourceState.UNREADABLE and own:
        text = _("admin.source.state.unreadable_own")
    else:
        text = _(STATE_TEXTS[state])
    return SourceFinding(state, text, "danger" if faults else STATE_CHIPS[state], faults)


def card_finding(
    _: BoundGettext,
    sources: NetworkList,
    *,
    own: bool,
    required: bool,
    chain: ProxyChain,
) -> SourceFinding:
    """Return the finding for the organisation's card.

    The same as ``source_finding`` except where whose list it is changes what to do: a default that
    cannot be read is not the organisation's to repair, so the card says whom to ask, or to set a
    list of its own.
    """
    found = source_finding(_, sources, own=own, required=required, chain=chain)
    if found.state is SourceState.UNREADABLE and not own:
        return replace(found, text=_("admin.source.state.unreadable_default_card"))
    return found


@dataclass(frozen=True)
class PauseFinding:
    """A paused connection as the card shows it (ADR 0124 point 9).

    Attributes:
        chip: The chip's word.
        text: The sentence: deliveries are accepted and dropped, the platform keeps its
            subscription, and how to resume.
    """

    chip: str
    text: str


def pause_finding(_: BoundGettext, *, enabled: bool) -> PauseFinding | None:
    """Return the finding of a paused connection, or ``None`` while it handles its events."""
    if enabled:
        return None
    return PauseFinding(_("admin.source.paused_chip"), _("admin.source.paused_text"))

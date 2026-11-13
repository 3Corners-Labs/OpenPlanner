"""What the portal says about where an address's webhooks may come from (ADR 0124 points 3, 4, 13).

The organisation's connection card and System status say the same thing about the state, in the
same words, so the judgement is made once here from the pure rule (``app.domain.webhook_sources``):

    list that applies, host's switch ─► ``SourceState``  ─► not restricted / restricted /
                                                           required and missing / unreadable
    proxy chain                       ─► ``ChainFault``s ─► what to repair, while a list applies

A finding is never a silent state: an address that any source may call says so, a list that cannot
be read says that every call is refused, and a list the gate cannot evaluate (the chain cannot be
trusted) says that its calls are refused. The detail of a fault names the host's configuration and
belongs to System status only; the organisation's card says that deliveries are refused because
of the installation's proxy setting and whom to ask, without naming the variable. The faults of
one call (the caller is the proxy, a hop did not hand it on, no address, headers its peer was not
trusted to send) are decided per call and show in the events, not here. Texts are in
``app/i18n``; nothing here holds an address of a caller.
"""

from dataclasses import dataclass

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
"""The faults that hold for every call, with the detail for System status."""
CARD_FAULT_TEXT = "admin.source.fault.card"
"""What the organisation's card says of any standing fault: the cause is the installation's."""


@dataclass(frozen=True)
class SourceFinding:
    """One address's source networks as the page shows them.

    Attributes:
        text: The state in words (not restricted, restricted to N networks, list missing or
            unreadable).
        chip: The tone of the chip: ``success``, ``warning`` or ``danger``.
        faults: Words for each reason a list would be refused as unevaluable, with what to
            repair; empty without a list or with a trustworthy chain.
    """

    text: str
    chip: str
    faults: tuple[str, ...]


def source_finding(
    _: BoundGettext,
    sources: NetworkList,
    *,
    own: bool,
    required: bool,
    chain: ProxyChain,
    detailed: bool,
) -> SourceFinding:
    """Return the finding for an address governed by ``sources``.

    Args:
        _: The request's translator.
        sources: The list that applies (a connection's own, else the default); empty: none.
        own: Whether the list is the connection's own rather than the installation's default.
        required: ``WEBHOOK_SOURCE_NETWORKS_REQUIRED``.
        chain: What is known about the proxy chain.
        detailed: Whether the faults name the host's configuration (System status) or only say
            that the installation's setting is the cause and whom to ask (the card).
    """
    state = source_state(sources, required=required)
    if state is SourceState.RESTRICTED:
        key = (
            "admin.source.state.restricted_own" if own else "admin.source.state.restricted_default"
        )
        text = _(key, count=len(sources.networks))
        standing = chain.standing_faults()
        faults = (
            tuple(_(FAULT_TEXTS[f]) for f in standing)
            if detailed
            else (_(CARD_FAULT_TEXT),) * bool(standing)
        )
    else:
        text, faults = _(STATE_TEXTS[state]), ()
    return SourceFinding(text, "danger" if faults else STATE_CHIPS[state], faults)

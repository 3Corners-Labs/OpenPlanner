"""What the portal says about where an address's webhooks may come from (ADR 0124 points 3, 4, 13).

The organisation's connection card and System status say the same thing in the same words, so the
judgement is made once here from the pure rule (``app.domain.webhook_sources``):

    list that applies, host's switch ─► ``SourceState``  ─► not restricted / restricted / missing
    proxy chain                       ─► ``ChainFault``s ─► what to repair, while a list applies

A finding is never a silent state: an address that any source may call says so, and a list the
gate cannot evaluate (the chain cannot be trusted) says that its calls are refused and what to
repair. Texts are in ``app/i18n``; nothing here holds an address of a caller.
"""

from dataclasses import dataclass

from app.domain.webhook_sources import ChainFault, ProxyChain, SourceState, source_state
from app.i18n import BoundGettext

STATE_CHIPS = {
    SourceState.UNRESTRICTED: "warning",
    SourceState.RESTRICTED: "success",
    SourceState.REQUIRED_MISSING: "danger",
}
STATE_TEXTS = {
    SourceState.UNRESTRICTED: "admin.source.state.unrestricted",
    SourceState.REQUIRED_MISSING: "admin.source.state.required_missing",
}
FAULT_TEXTS = {ChainFault.WIDE_TRUST: "admin.source.fault.wide_trust"}
"""The faults that hold for every call; the per-call ones are told by the refused event."""


@dataclass(frozen=True)
class SourceFinding:
    """One address's source networks as the page shows them.

    Attributes:
        text: The state in words (not restricted, restricted to N networks, list missing).
        chip: The tone of the chip: ``success``, ``warning`` or ``danger``.
        faults: Words for each reason a list would be refused as unevaluable, with what to
            repair; empty without a list or with a trustworthy chain.
    """

    text: str
    chip: str
    faults: tuple[str, ...]


def source_finding(
    _: BoundGettext,
    networks: tuple[str, ...],
    *,
    own: bool,
    required: bool,
    chain: ProxyChain,
) -> SourceFinding:
    """Return the finding for an address governed by ``networks``.

    Args:
        _: The request's translator.
        networks: The list that applies (a connection's own, else the default); empty: none.
        own: Whether the list is the connection's own rather than the installation's default.
        required: ``WEBHOOK_SOURCE_NETWORKS_REQUIRED``.
        chain: What is known about the proxy chain.
    """
    state = source_state(networks, required=required)
    if state is SourceState.RESTRICTED:
        key = (
            "admin.source.state.restricted_own" if own else "admin.source.state.restricted_default"
        )
        text = _(key, count=len(networks))
        faults = tuple(_(FAULT_TEXTS[f]) for f in chain.standing_faults())
    else:
        text, faults = _(STATE_TEXTS[state]), ()
    return SourceFinding(text, "danger" if faults else STATE_CHIPS[state], faults)

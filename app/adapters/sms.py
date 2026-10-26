"""What every SMS gateway adapter shares: its configuration and the GSM 7-bit check (ADR 0028).

One adapter per ``SmsProvider`` implements ``MessageSender`` on top of this; the registry
in ``app.adapters.senders`` picks it by the organisation's stored provider. Nothing here
knows a provider's API.
"""

from dataclasses import dataclass, field

GSM_7BIT = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
"""The GSM 03.38 basic character set; other characters need the (shorter) unicode encoding."""


@dataclass(frozen=True)
class SmsGatewayConfig:
    """Where and as whom to send, whatever the provider.

    Attributes:
        api_base_url: The provider's API host for the organisation.
        api_key: The provider's API key or token; never logged.
        sender_id: Alphanumeric sender or registered number.
        country_code: For national numbers without ``+`` (``31``).
    """

    api_base_url: str
    api_key: str = field(repr=False)
    sender_id: str
    country_code: str = "31"


def fits_gsm_7bit(text: str) -> bool:
    """Return whether every character of ``text`` is in the GSM 7-bit basic set."""
    return set(text) <= GSM_7BIT

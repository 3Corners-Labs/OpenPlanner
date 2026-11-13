"""The rules of the inbound webhook gate: refusal reasons, their statuses, size and media type.

Every call under ``/webhooks/`` passes one gate (ADR 0124): ``app.web.webhook_gate`` is its HTTP
part, this module the pure rules it applies. A refusal has one reason out of a closed list, one
HTTP status and an empty answer; the reason is what a security event records. The list is fixed
on purpose: a reason that is not in it cannot be written, and a new one needs an ADR.

The order of the checks, cheapest first (ADR 0124 point 2): the address exists, the source
network, the rate limit, the content type, the size, the signature, its time, the replay, the
shape. Reasons that a later step of the ADR produces (``network``, ``source_unknown``) are
already part of the list.
"""

from enum import StrEnum

from app.domain.security_log import INVALID_SIGNATURE

MAX_BODY_BYTES = 64 * 1024
"""The largest webhook body in bytes. A real event is a few kilobytes (ADR 0124 point 6)."""

ACCEPTED_MEDIA_TYPES = frozenset({"application/json", "application/cloudevents+json"})
"""The media types a webhook may be sent as; parameters such as ``charset`` are ignored."""


class Refusal(StrEnum):
    """Why the gate refused a call: the closed list of ADR 0124 point 10.

    The value is the code written to the security event and the log; it never carries anything
    from the request.
    """

    UNKNOWN_ADDRESS = "unknown_address"
    """The platform, organisation or connection does not exist, or has no secret."""
    NETWORK = "network"
    """The caller's address is outside the allowed networks."""
    SOURCE_UNKNOWN = "source_unknown"
    """A restriction applies and the caller's address cannot be trusted."""
    RATE = "rate"
    """Too many calls from this source to this address."""
    CONTENT_TYPE = "content_type"
    """The call names a content type that is not JSON."""
    TOO_LARGE = "too_large"
    """The body is over the size limit, declared or actually sent."""
    SIGNATURE = INVALID_SIGNATURE
    """The signature does not verify under the secret.

    Its value stays ``invalid_signature``, the name audit rows and log filters already carry
    (ADR 0116: a persisted name is not renamed in place).
    """
    STALE = "stale"
    """The signature verifies, but the signed time is too far from now."""
    MALFORMED = "malformed"
    """The body is verified but not an object of the expected shape."""

    @property
    def status(self) -> int:
        """Return the HTTP status that answers this reason."""
        return STATUS[self]


STATUS: dict[Refusal, int] = {
    Refusal.UNKNOWN_ADDRESS: 404,
    Refusal.NETWORK: 403,
    Refusal.SOURCE_UNKNOWN: 403,
    Refusal.RATE: 429,
    Refusal.CONTENT_TYPE: 415,
    Refusal.TOO_LARGE: 413,
    Refusal.SIGNATURE: 401,
    Refusal.STALE: 401,
    Refusal.MALFORMED: 400,
}
"""The HTTP status per reason (ADR 0124 point 2). Every refusal answers with an empty body."""


def accepts_content_type(value: str | None) -> bool:
    """Return whether a ``Content-Type`` header is no reason to refuse a webhook.

    A call without the header (or with an empty one) passes: the signature decides, and the
    video platform's real deliveries were never observed to send it. A header that is there must
    name one of the two JSON media types; only the media type is compared, case-insensitively,
    and parameters such as ``; charset=utf-8`` are ignored.

    Args:
        value: The header value, or ``None`` when the call sent none.
    """
    if not value:
        return True
    return value.split(";", 1)[0].strip().lower() in ACCEPTED_MEDIA_TYPES


def declared_length_acceptable(value: str | None, limit: int = MAX_BODY_BYTES) -> bool:
    """Return whether a ``Content-Length`` header is no reason to refuse the body unread.

    A missing or non-numeric length is not a refusal: a chunked body has none, and it is capped
    while it is read. Only a number over ``limit`` refuses at once.

    Args:
        value: The header value, or ``None``.
        limit: The largest body in bytes.
    """
    if value is None:
        return True
    text = value.strip()
    if not (text.isascii() and text.isdigit()):
        return True
    return int(text) <= limit

"""What the participant's call page offers besides the call itself (ADR 0123 point 11).

A pure rule, no framework: whether the participant may be shown a way into the video platform's
own app next to the page, from the organisation's choice ``embedded_call_webex_option``. The
link reads "join another way" and never names the platform (point 14).
"""

from enum import StrEnum


class OtherWayOffer(StrEnum):
    """The values of ``embedded_call_webex_option``.

    ``HIDDEN`` (the default) shows no way into the platform's app; ``ON_FAILURE`` shows it only
    when the page cannot connect after its retries or cannot run in this browser; ``ALWAYS``
    shows it before joining as well.
    """

    HIDDEN = "hidden"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"

    @classmethod
    def of(cls, value: str) -> "OtherWayOffer":
        """Return the offer named by a stored setting value; anything unknown is ``HIDDEN``.

        The page fails closed: a value that is none of the three never shows a way into the
        platform's own app.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.HIDDEN


def shows_other_way(offer: OtherWayOffer, *, failed: bool) -> bool:
    """Return whether the page shows the link "join another way".

    Args:
        offer: The organisation's choice.
        failed: ``True`` in a state where our page cannot give the call: the browser cannot run
            it, or it could not connect. ``False`` before joining.
    """
    if offer is OtherWayOffer.ALWAYS:
        return True
    return failed and offer is OtherWayOffer.ON_FAILURE

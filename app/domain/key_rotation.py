"""What a key rotation still has to move, as counts (ADR 0094 points 3 and 4).

Pure counting for System status: how many sealed records are still under
``SETTINGS_ENCRYPTION_KEY_PREVIOUS`` and how many open bookings still carry a management
link made under ``MANAGEMENT_TOKEN_KEY_PREVIOUS``. Only numbers leave this module; no key,
token, salt or hash is returned.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.tokens import derive_token, token_matches


@dataclass(frozen=True)
class BookingCredential:
    """What the database keeps of a booking's management link: its salt and the token's hash.

    Attributes:
        salt: The booking's random salt (empty for a booking from before salts).
        token_hash: Hex SHA-256 of the booking's token.
    """

    salt: str
    token_hash: str


def bookings_on_previous_key(
    credentials: Iterable[BookingCredential], current: bytes, previous: bytes
) -> int:
    """Return how many bookings have a link that matches the previous key and not the current.

    The key id is not stored with a booking, so the link is derived again under the current
    key and compared with the stored hash (ADR 0094 point 4 does the same when a message is
    built). A booking that matches neither key is not counted: no setting of the keys can
    change that.
    """
    return sum(
        1
        for credential in credentials
        if credential.salt
        and not token_matches(derive_token(current, credential.salt), credential.token_hash)
        and token_matches(derive_token(previous, credential.salt), credential.token_hash)
    )


@dataclass(frozen=True)
class RotationStatus:
    """The two counters of System status; ``None`` when that previous key is not set.

    Attributes:
        records: Sealed records not under the current settings key.
        bookings: Open bookings whose link is under the previous token key.
    """

    records: int | None
    bookings: int | None

    @property
    def active(self) -> bool:
        """Return whether a rotation is under way, so the block is worth showing."""
        return self.records is not None or self.bookings is not None

    @property
    def records_done(self) -> bool:
        """Return whether ``SETTINGS_ENCRYPTION_KEY_PREVIOUS`` is set and nothing needs it."""
        return self.records == 0

    @property
    def bookings_done(self) -> bool:
        """Return whether ``MANAGEMENT_TOKEN_KEY_PREVIOUS`` is set and no open booking needs it."""
        return self.bookings == 0

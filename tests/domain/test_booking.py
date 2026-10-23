from datetime import UTC, datetime

import pytest

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import PhoneMode

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def contact(**overrides: object) -> Contact:
    fields: dict[str, object] = {
        "name": "Ada Lovelace",
        "email": "ada@example.org",
        "phone": None,
        "phone_mode": PhoneMode.OPTIONAL,
    }
    fields.update(overrides)
    return Contact.create(**fields)  # type: ignore[arg-type]


def test_contact_is_normalised() -> None:
    c = contact(name="  Ada   Lovelace ", email=" ada@Example.org ", phone="06-1234 5678")

    assert c.name == "Ada Lovelace"
    assert c.email == "ada@Example.org"
    assert c.phone == "0612345678"
    assert c.first_name == "Ada"


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+31 6 12345678", "+31612345678"),
        ("0031 6 1234 5678", "+31612345678"),
        ("(020) 123.45.67", "0201234567"),
    ],
)
def test_phone_numbers_are_normalised(phone: str, expected: str) -> None:
    assert contact(phone=phone).phone == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "   "),
        ("name", "x" * 101),
        ("name", "Ada\x00"),
        ("email", "no-at-sign"),
        ("email", "two@@example.org"),
        ("email", "@example.org"),
        ("email", "ada@localhost"),
        ("email", "ada lovelace@example.org"),
        ("email", "ada@exa\nmple.org"),
        ("email", "a" * 250 + "@x.org"),
        ("phone", "12345"),
        ("phone", "+31 6 1234 5678 9012 34"),
        ("phone", "06-CALL-ME"),
    ],
)
def test_invalid_contact_names_the_field_only(field: str, value: str) -> None:
    with pytest.raises(DomainError) as excinfo:
        contact(**{field: value})

    assert excinfo.value.code is ErrorCode.INVALID_CONTACT
    assert excinfo.value.field == field
    assert value.strip() not in str(excinfo.value) or not value.strip()


def test_phone_required_by_the_group() -> None:
    with pytest.raises(DomainError) as excinfo:
        contact(phone="  ", phone_mode=PhoneMode.REQUIRED)

    assert excinfo.value.field == "phone"


def test_phone_is_dropped_when_the_group_hides_it() -> None:
    assert contact(phone="0612345678", phone_mode=PhoneMode.HIDDEN).phone is None


def test_optional_phone_may_be_empty() -> None:
    assert contact(phone="").phone is None


def make_booking(status: BookingStatus = BookingStatus.CONFIRMED) -> Booking:
    return Booking(
        id=1,
        group_id=1,
        slot_id=1,
        start=NOW,
        end=NOW,
        status=status,
        contact=contact(),
        locale="nl",
        token_hash="h",
        created_at=NOW,
    )


def test_cancel_sets_status_and_time() -> None:
    cancelled = make_booking().cancel(NOW)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.cancelled_at == NOW
    assert cancelled.is_active is False


@pytest.mark.parametrize(
    "status", [BookingStatus.CANCELLED, BookingStatus.COMPLETED, BookingStatus.NO_SHOW]
)
def test_only_confirmed_bookings_can_be_cancelled(status: BookingStatus) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_booking(status).cancel(NOW)

    assert excinfo.value.code is ErrorCode.BOOKING_NOT_ACTIVE


def test_booking_repr_hides_personal_data() -> None:
    text = repr(make_booking())

    assert "Ada" not in text
    assert "example.org" not in text

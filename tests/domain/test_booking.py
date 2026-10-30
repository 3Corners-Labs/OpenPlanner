from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.errors import DomainError, ErrorCode
from app.domain.scheduling import PhoneMode

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def contact(**overrides: object) -> Contact:
    fields: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.org",
        "phone": None,
        "phone_mode": PhoneMode.OPTIONAL,
    }
    fields.update(overrides)
    return Contact.create(**fields)  # type: ignore[arg-type]


def test_contact_is_normalised() -> None:
    c = contact(
        first_name="  Ada  Augusta ",
        last_name=" Lovelace ",
        email=" ada@Example.org ",
        phone="06-1234 5678",
    )

    assert c.name == "Ada Augusta Lovelace"
    assert c.email == "ada@Example.org"
    assert c.phone == "+31612345678"
    assert c.first_name == "Ada Augusta"
    assert c.last_name == "Lovelace"


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("+31 6 12345678", "+31612345678"),
        ("0031 6 1234 5678", "+31612345678"),
        ("(020) 123.45.67", "+31201234567"),
        ("+32 470 12 34 56", "+32470123456"),
    ],
)
def test_phone_numbers_are_stored_in_e164(phone: str, expected: str) -> None:
    assert contact(phone=phone).phone == expected


def test_the_organisation_region_reads_numbers_without_a_country_code() -> None:
    assert contact(phone="0470 12 34 56", region="BE").phone == "+32470123456"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", ""),
        ("first_name", "   "),
        ("first_name", "x" * 101),
        ("first_name", "Ada\x00"),
        ("last_name", ""),
        ("last_name", "x" * 101),
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


def test_longest_allowed_values_pass() -> None:
    longest_email = "a" * 242 + "@example.org"  # 254 characters

    c = contact(first_name="x" * 100, last_name="y" * 100, email=longest_email)

    assert c.email == longest_email


def test_email_domain_needs_a_dot_between_labels() -> None:
    with pytest.raises(DomainError, match="email"):
        contact(email="ada@.org.")
    with pytest.raises(DomainError, match="email"):
        contact(email="ada@..")


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


def test_pending_booking_holds_its_place_but_is_not_active() -> None:
    pending = make_booking(BookingStatus.PENDING_CONFIRMATION)

    assert pending.holds_place is True
    assert pending.is_active is False
    assert pending.awaits_confirmation is True


def test_confirm_records_the_moment_the_address_was_verified() -> None:
    confirmed = make_booking(BookingStatus.PENDING_CONFIRMATION).confirm(NOW)

    assert confirmed.status is BookingStatus.CONFIRMED
    assert confirmed.confirmed_at == NOW
    assert confirmed.awaits_confirmation is False


@pytest.mark.parametrize("status", [BookingStatus.CONFIRMED, BookingStatus.CANCELLED])
def test_only_pending_bookings_can_be_confirmed(status: BookingStatus) -> None:
    with pytest.raises(DomainError) as excinfo:
        make_booking(status).confirm(NOW)

    assert excinfo.value.code is ErrorCode.BOOKING_NOT_PENDING


def test_expire_cancels_an_unconfirmed_booking_at_its_deadline() -> None:
    deadline = NOW - timedelta(minutes=3)  # the worker ran a little late
    pending = replace(make_booking(BookingStatus.PENDING_CONFIRMATION), confirm_by=deadline)

    lapsed = pending.expire(NOW)

    assert lapsed.status is BookingStatus.CANCELLED
    assert lapsed.cancelled_at == deadline
    assert lapsed.holds_place is False
    assert lapsed.lapsed_unconfirmed is True


def test_a_cancelled_confirmed_booking_did_not_lapse_unconfirmed() -> None:
    confirmed = make_booking(BookingStatus.PENDING_CONFIRMATION).confirm(NOW)

    assert confirmed.cancel(NOW).lapsed_unconfirmed is False


def test_a_confirmed_booking_cannot_expire() -> None:
    with pytest.raises(DomainError) as excinfo:
        make_booking().expire(NOW)

    assert excinfo.value.code is ErrorCode.BOOKING_NOT_PENDING


def test_a_pending_booking_can_be_cancelled_by_the_citizen() -> None:
    cancelled = make_booking(BookingStatus.PENDING_CONFIRMATION).cancel(NOW)

    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.lapsed_unconfirmed is False

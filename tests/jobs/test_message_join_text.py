"""What the confirmation says about how the call opens, per join client (ADR 0123 step 6a).

Every client has its sentence; the one for an organisation's own page names no supplier and is true
whether the call opens in our page or, while the platform switch is off, in the web app.
"""

import pytest

from app.domain.booking import Booking, BookingStatus, Contact
from app.domain.job_plan import JobType
from app.domain.scheduling import JoinClient, PhoneMode
from app.domain.tokens import new_token_salt
from app.i18n import Translator, load_catalogues
from app.jobs.messages import _JOIN_TEXT_KEYS, MessageComposer
from tests.support.domain import NOW, SLOT_0900, SLOT_0930, TEST_TOKEN_KEY, make_group

BOOKING = Booking(
    id=42,
    group_id=1,
    slot_id=1,
    start=SLOT_0900,
    end=SLOT_0930,
    status=BookingStatus.CONFIRMED,
    contact=Contact.create(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.org",
        phone=None,
        phone_mode=PhoneMode.OPTIONAL,
    ),
    locale="nl",
    token_hash="h",
    created_at=NOW,
    token_salt=new_token_salt(),
)


@pytest.fixture(scope="module")
def translator() -> Translator:
    return Translator(load_catalogues())


def test_every_client_has_a_sentence() -> None:
    assert set(_JOIN_TEXT_KEYS) == set(JoinClient)


@pytest.mark.parametrize("client", list(JoinClient))
def test_the_message_carries_the_sentence_of_the_groups_client(
    translator: Translator, client: JoinClient
) -> None:
    composer = MessageComposer(translator, TEST_TOKEN_KEY, "https://op.example.nl")
    group = make_group(video_host_email="host@example.nl", join_client=client)

    message = composer.compose(JobType.SEND_CONFIRMATION, BOOKING, group, NOW)

    assert translator.gettext("nl", _JOIN_TEXT_KEYS[client]) in message.text


def test_the_sentence_for_the_organisations_own_page_names_no_supplier(
    translator: Translator,
) -> None:
    key = _JOIN_TEXT_KEYS[JoinClient.EMBEDDED]

    for locale in ("nl", "en"):
        text = translator.gettext(locale, key)
        assert text != key  # it exists in the catalogue
        assert "Webex" not in text

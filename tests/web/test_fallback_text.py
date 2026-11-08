"""The team-space messages of a fallback (ADR 0082 point 12): never the join link."""

from dataclasses import replace

from app.domain.booking import MeetingRef
from app.i18n import Translator, load_catalogues
from app.web.fallback_text import SpaceTexts
from tests.support.closures import confirmed_booking
from tests.support.domain import make_group


def test_a_line_holds_time_first_name_and_the_queue_never_the_join_link() -> None:
    texts = SpaceTexts(Translator(load_catalogues()).gettext, "https://plan.voorbeeld.nl")
    group = make_group(slug="team", name="Team")
    booking = replace(
        confirmed_booking(), meeting=MeetingRef("m-1", "https://site.webex.com/meet/secret")
    )
    for locale in ("nl", "en"):
        line = texts.queued(group, booking, locale)
        assert "Ada" in line
        assert "https://plan.voorbeeld.nl/admin/groups/team/queue" in line
        assert "webex.com" not in line
        assert "Lovelace" not in line
        assert "Team" in texts.activated(group, locale)


def test_a_first_name_never_becomes_a_link_or_a_mention_in_the_space() -> None:
    texts = SpaceTexts(Translator(load_catalogues()).gettext, "https://plan.voorbeeld.nl")
    group = make_group(slug="team", name="Team *one*")
    booking = confirmed_booking()
    assert booking.contact is not None
    booking = replace(
        booking, contact=replace(booking.contact, first_name="[Open](https://evil.example) <@x>")
    )
    line = texts.queued(group, booking, "nl")
    assert "[Open](" not in line
    assert r"\[Open\]\(https\://evil\.example\) \<\@x\>" in line
    assert r"Team \*one\*" in texts.activated(group, "nl")

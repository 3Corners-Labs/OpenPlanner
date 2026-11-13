"""The installation's default lists as the gate reads them: unreadable is never empty (ADR 0124)."""

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.domain.networks import NetworkList
from app.web.webhook_sources import (
    CONTACT_CENTER_KEY,
    VIDEO_KEY,
    contact_center_networks,
    video_networks,
)
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW


class FakeLive:
    """The one thing the reading asks of ``LiveSettings``: is the stored value ignored?"""

    def __init__(self, ignored: frozenset[str] = frozenset()) -> None:
        self._ignored = ignored

    def portal_ignored(self, key: str) -> bool:
        return key in self._ignored


def request(settings: Settings, live: FakeLive | None = None) -> SimpleNamespace:
    state = SimpleNamespace(settings=settings, clock=FixedClock(NOW))
    if live is not None:
        state.live_settings = live
    return SimpleNamespace(app=SimpleNamespace(state=state))


SETTINGS = Settings(environment="test")


@pytest.mark.parametrize(
    ("key", "read"), [(VIDEO_KEY, video_networks), (CONTACT_CENTER_KEY, contact_center_networks)]
)
class TestADefaultThatIsStored:
    def test_a_default_that_is_set_is_read(self, key: str, read) -> None:  # type: ignore[no-untyped-def]
        shown = read(request(SETTINGS.model_copy(update={key: "8.8.8.0/24, 8.8.4.0/24"})))

        assert shown == NetworkList(("8.8.8.0/24", "8.8.4.0/24"))

    def test_without_a_value_it_is_empty_and_readable(self, key: str, read) -> None:  # type: ignore[no-untyped-def]
        assert read(request(SETTINGS, FakeLive())) == NetworkList()

    @pytest.mark.parametrize("text", ["not-a-network", "0.0.0.0/0", "::/0"])
    def test_a_value_that_does_not_parse_is_unreadable(self, key: str, read, text: str) -> None:  # type: ignore[no-untyped-def]
        shown = read(request(SETTINGS.model_copy(update={key: text})))

        assert shown == NetworkList(unreadable=True)

    def test_a_stored_value_the_settings_ignore_is_unreadable_here(self, key: str, read) -> None:  # type: ignore[no-untyped-def]
        """The settings fall back to the empty default: that must not read as "any source"."""
        shown = read(request(SETTINGS, FakeLive(frozenset({key}))))

        assert shown == NetworkList(unreadable=True)

    def test_a_stored_value_the_settings_apply_is_read(self, key: str, read) -> None:  # type: ignore[no-untyped-def]
        shown = read(request(SETTINGS.model_copy(update={key: "8.8.8.0/24"}), FakeLive()))

        assert shown == NetworkList(("8.8.8.0/24",))

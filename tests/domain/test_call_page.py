"""What the call page offers besides the call (ADR 0123 point 11)."""

import pytest

from app.domain.call_page import OtherWayOffer, shows_other_way
from app.domain.settings_registry import BY_KEY, SettingClass


@pytest.mark.parametrize(
    ("offer", "before", "failed"),
    [
        (OtherWayOffer.HIDDEN, False, False),
        (OtherWayOffer.ON_FAILURE, False, True),
        (OtherWayOffer.ALWAYS, True, True),
    ],
)
def test_each_value_shows_the_link_where_the_adr_says(
    offer: OtherWayOffer, before: bool, failed: bool
) -> None:
    assert shows_other_way(offer, failed=False) is before
    assert shows_other_way(offer, failed=True) is failed


def test_the_setting_is_an_organisation_setting_that_defaults_to_hidden() -> None:
    spec = BY_KEY["embedded_call_webex_option"]

    assert spec.default == "hidden"
    assert spec.choices == ("hidden", "on_failure", "always")
    assert spec.class_ is SettingClass.ORGANISATION

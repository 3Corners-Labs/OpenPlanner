"""The settings of the webhook source networks (ADR 0124 point 3, step 2b)."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.platform_settings import usable
from app.domain.settings_registry import REGISTRY, SettingClass, SettingSpec

LISTS = ("webhook_source_networks_video", "webhook_source_networks_contact_center")


def spec(key: str) -> SettingSpec:
    return next(s for s in REGISTRY if s.key == key)


@pytest.mark.parametrize("key", LISTS)
def test_the_installations_default_lists_are_platform_settings_empty_by_default(key: str) -> None:
    entry = spec(key)

    assert (entry.class_, entry.default) == (SettingClass.PLATFORM, "")
    assert entry.permission is not None
    assert entry.permission.value == "settings.platform.manage"
    assert getattr(Settings(environment="test"), key) == ""


def test_whether_a_list_is_required_is_a_host_setting_never_in_the_portal() -> None:
    required = spec("webhook_source_networks_required")

    assert (required.class_, required.default) == (SettingClass.DEPLOYMENT, False)
    assert not required.in_portal
    assert "protection" in required.description


@pytest.mark.parametrize("key", LISTS)
def test_the_portal_refuses_a_value_that_is_no_list_of_at_most_32_networks(key: str) -> None:
    entry = spec(key)
    too_many = ",".join(f"10.0.{n}.0/24" for n in range(33))

    assert usable(entry, "")
    assert usable(entry, "8.8.8.0/24, 2001:4860:4860::/48")
    assert usable(entry, "8.8.8.0/24\n8.8.9.0/24")
    assert not usable(entry, "not-a-network")
    assert not usable(entry, "8.8.8.0/24, 300.1.1.1")
    assert not usable(entry, "0.0.0.0/0")  # a whole address family is "any source"
    assert not usable(entry, "8.8.8.0/24, ::/0")
    assert not usable(entry, too_many)
    assert not usable(entry, 7)


@pytest.mark.parametrize("key", LISTS)
def test_the_environment_cannot_hold_a_wrong_list_either(key: str) -> None:
    assert getattr(Settings(environment="test", **{key: "8.8.8.0/24"}), key) == "8.8.8.0/24"
    for wrong in ("nonsense", "0.0.0.0/0", "::/0", ",".join(f"10.0.{n}.0/24" for n in range(33))):
        with pytest.raises(ValidationError) as refused:
            Settings(environment="test", **{key: wrong})
        assert key.upper() in str(refused.value)

"""The environment bar of the admin portal: shown in dev and test, never in production."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.web.templates import EnvironmentNotice, environment_of


def _request(**settings: Any) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(**settings)))
    )


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("dev", EnvironmentNotice("admin.environment.dev", simulator=False)),
        ("test", EnvironmentNotice("admin.environment.test", simulator=False)),
        ("prod", None),
    ],
)
def test_the_bar_follows_the_environment(environment: str, expected: object) -> None:
    assert environment_of(_request(environment=environment, simulator_enabled=False)) == expected


def test_the_bar_says_when_the_simulator_is_on() -> None:
    notice = environment_of(_request(environment="test", simulator_enabled=True))
    assert notice is not None
    assert notice.simulator


def test_without_settings_the_app_counts_as_production() -> None:
    assert environment_of(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))) is None

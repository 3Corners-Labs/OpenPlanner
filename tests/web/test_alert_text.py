"""The alert as it leaves OpenPlanner (ADR 0082 point 10): codes only, never participant data."""

from datetime import UTC, datetime

from app.domain.alert_delivery import trial_alert
from app.domain.alert_model import AlertChannelKind, AlertTarget
from app.domain.alerts import Alert, AlertLevel
from app.i18n import Translator, load_catalogues
from app.web.alert_text import alert_composer

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
TARGET = AlertTarget(1, 1, AlertChannelKind.EMAIL, "ops@voorbeeld.nl", "warning")


def test_the_rendered_alert_holds_codes_a_link_and_the_version_only() -> None:
    compose = alert_composer(
        Translator(load_catalogues()).gettext,
        "https://plan.voorbeeld.nl",
        "1.2.3",
        "Europe/Amsterdam",
    )
    alert = Alert(
        "integration.health",
        AlertLevel.OUTAGE,
        "down",
        "demo",
        "team",
        "",
        NOW,
        NOW,
        "platform:wxcc",
        "abc123",
    )
    for locale in ("nl", "en"):
        message = compose(alert, TARGET, 2, locale)
        for part in (message.subject, message.text, message.markdown):
            assert "integration.health" in part
        assert "https://plan.voorbeeld.nl/admin/audit?trace=abc123" in message.text
        assert "1.2.3" in message.text
        assert "2" in message.text
        assert "ops@voorbeeld.nl" not in message.text
    assert compose(trial_alert(NOW, None), TARGET, 0, "en").subject == "OpenPlanner: test alert"

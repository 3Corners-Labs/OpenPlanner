"""An alert written out for email and a Webex space (ADR 0082 points 9 and 10).

A fixed layout with i18n text blocks (ADR 0009: no editable template): the level, what the
alert is about (its action code), the reason code, the organisation and group slugs, since
when, how many reports were counted since the last message, the version and a link into the
portal. Never a name, address, note, join link or token: an alert holds none of those.
"""

from collections.abc import Callable
from zoneinfo import ZoneInfo

from app.domain.alert_delivery import TEST_ALERT
from app.domain.alert_model import AlertTarget, RenderedAlert
from app.domain.alerts import Alert, AlertLevel

LEVEL_KEYS: dict[AlertLevel, str] = {
    AlertLevel.INFO: "alert.message.level.info",
    AlertLevel.WARNING: "alert.message.level.warning",
    AlertLevel.OUTAGE: "alert.message.level.outage",
}
"""The level in words; the catalogue keys are written out so the catalogue test finds them."""

Gettext = Callable[..., str]


def alert_link(base_url: str, alert: Alert) -> str:
    """Return where the alert leads in the portal: the overview's alerts, or the log's chain."""
    base = base_url.rstrip("/")
    if alert.trace_id:
        return f"{base}/admin/audit?trace={alert.trace_id}"
    return f"{base}/admin/"


def compose_alert(
    alert: Alert,
    target: AlertTarget,
    suppressed: int,
    gettext: Gettext,
    *,
    base_url: str,
    version: str,
    when: str,
) -> RenderedAlert:
    """Return the subject, plain text and markdown of ``alert`` for ``target``.

    Args:
        alert: The open alert (codes only).
        target: Where it goes; its kind does not change the words.
        suppressed: Reports of the alert counted since the last message.
        gettext: Translates a key in the target's locale.
        base_url: The portal's public address, for the link.
        version: The running version.
        when: The alert's start, formatted at the edge.
    """
    level = gettext(LEVEL_KEYS[alert.level])
    if alert.action == TEST_ALERT:
        subject = gettext("alert.message.test.subject")
        lines = [gettext("alert.message.test.text")]
    else:
        subject = gettext("alert.message.subject", level=level, action=alert.action)
        lines = [
            gettext("alert.message.what", action=alert.action, reason=alert.reason or "-"),
            gettext("alert.message.since", time=when),
        ]
        if alert.organisation:
            lines.append(gettext("alert.message.organisation", organisation=alert.organisation))
        if alert.group:
            lines.append(gettext("alert.message.group", group=alert.group))
        if suppressed:
            lines.append(gettext("alert.message.repeated", count=suppressed))
    link = alert_link(base_url, alert)
    lines += [
        gettext("alert.message.link", link=link),
        gettext("alert.message.version", version=version),
    ]
    text = "\n".join(lines)
    markdown = f"**{subject}**\n\n" + "\n\n".join(lines)
    return RenderedAlert(subject, text, markdown)


def alert_composer(
    gettext: Callable[..., str], base_url: str, version: str, timezone: str
) -> Callable[[Alert, AlertTarget, int, str], RenderedAlert]:
    """Return ``compose(alert, target, suppressed, locale)`` for the notifier and the test alert.

    ``gettext`` is ``Translator.gettext`` (locale first); times are shown in ``timezone``
    (``ADMIN_TIMEZONE``).
    """
    zone = ZoneInfo(timezone)

    def compose(alert: Alert, target: AlertTarget, suppressed: int, locale: str) -> RenderedAlert:
        def translate(key: str, **params: object) -> str:
            return gettext(locale, key, **params)

        when = alert.since.astimezone(zone).strftime("%Y-%m-%d %H:%M")
        return compose_alert(
            alert, target, suppressed, translate, base_url=base_url, version=version, when=when
        )

    return compose

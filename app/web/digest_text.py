"""The digest mail in words: a fixed layout with text blocks from the catalogue (ADR 0009, 0117).

The layout is code; the words are ``reports.digest.*`` keys of ``app/i18n`` that an organisation
may override, with ``{placeholder}``s from the allow-list ``{organisation}``, ``{group}``,
``{period}``, ``{bookings}``, ``{no_show_rate}``, ``{average_wait}``, ``{utilisation}`` and
``{link}`` only. A hidden figure reads as a dash. There is a plain-text part and a semantic HTML
part (a language, a heading, a definition list, a link); no logo and no attachment.
"""

from collections.abc import Callable
from html import escape

from app.domain.report_digest_model import DigestContent, DigestMail

Gettext = Callable[..., str]
DASH = "–"
"""What a figure that is hidden reads as."""


def _number(value: int | None, suffix: str = "") -> str:
    """Return a figure with its unit, or a dash when it is hidden."""
    return DASH if value is None else f"{value}{suffix}"


def compose_digest(content: DigestContent, gettext: Callable[..., str], locale: str) -> DigestMail:
    """Return the mail for ``content`` in ``locale``.

    Args:
        content: What to say (codes, days, a link, perhaps figures).
        gettext: ``Translator.gettext``; locale first.
        locale: The language of the mail.
    """

    def say(key: str, **params: object) -> str:
        return gettext(
            locale,
            key,
            organisation_id=content.organisation_id,
            group_id=content.group_id,
            **params,
        )

    if content.first_day == content.last_day:
        period = f"{content.first_day:%Y-%m-%d}"
    else:
        period = f"{content.first_day:%Y-%m-%d} – {content.last_day:%Y-%m-%d}"
    figures = content.figures
    params: dict[str, object] = {
        "organisation": content.organisation_slug,
        "group": content.group_slug or say("reports.digest.all_groups"),
        "period": period,
        "link": content.link,
        "bookings": _number(figures.bookings if figures else None),
        "no_show_rate": _number(figures.no_show_percent if figures else None, " %"),
        "average_wait": _number(figures.average_wait_minutes if figures else None, " min"),
        "utilisation": _number(figures.utilisation_percent if figures else None, " %"),
    }
    subject = say("reports.digest.subject", **params)
    intro = say("reports.digest.intro", **params)
    rows = (
        (say("reports.digest.label.bookings"), params["bookings"]),
        (say("reports.digest.label.no_show_rate"), params["no_show_rate"]),
        (say("reports.digest.label.average_wait"), params["average_wait"]),
        (say("reports.digest.label.utilisation"), params["utilisation"]),
    )
    link_line = say("reports.digest.link", **params)
    footer = say("reports.digest.footer", **params)
    lines = [intro, ""]
    if figures is not None:
        lines += [f"{label}: {value}" for label, value in rows] + [""]
    lines += [link_line, "", footer]
    text = "\n".join(lines)
    body = [f"<h1>{escape(subject)}</h1>", f"<p>{escape(intro)}</p>"]
    if figures is not None:
        items = "".join(
            f"<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>" for label, value in rows
        )
        body.append(f"<dl>{items}</dl>")
    body.append(f'<p><a href="{escape(content.link, quote=True)}">{escape(link_line)}</a></p>')
    body.append(f"<p>{escape(footer)}</p>")
    html = (
        f'<!doctype html><html lang="{escape(locale)}"><head><meta charset="utf-8">'
        f"<title>{escape(subject)}</title></head><body>{''.join(body)}</body></html>"
    )
    return DigestMail(subject, text, html)

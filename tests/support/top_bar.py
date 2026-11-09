"""The admin top bar's links as a page renders them, for tests of the top bar and the alerts."""

import re
from dataclasses import dataclass

LINK = re.compile(r'<a class="admin-topbar__link[^"]*" href="([^"]+)"([^>]*)>(.*?)</a>', re.DOTALL)


@dataclass(frozen=True)
class TopLink:
    """One link of the top bar as the page renders it."""

    href: str
    label: str
    count: str | None
    aria_label: str | None
    name: str


def top_bar_links(page: str) -> list[TopLink]:
    """Return the links of the top bar's end, in order, before the profile menu."""
    end = page.split('<div class="admin-topbar__end">', 1)[1].split(
        '<details class="admin-profile">'
    )[0]
    links = []
    for href, attrs, inner in LINK.findall(end):
        label = re.search(r'admin-topbar__label">([^<]+)<', inner)
        count = re.search(r'<span class="chip chip--accent">([^<]+)</span>', inner)
        aria = re.search(r'aria-label="([^"]+)"', attrs)
        links.append(
            TopLink(
                href,
                label.group(1) if label else "",
                count.group(1) if count else None,
                aria.group(1) if aria else None,
                " ".join(re.sub(r"<[^>]+>", " ", inner).split()),
            )
        )
    return links

"""The delivery block of the connection card as markup and its empty state (ADR 0124 point 10)."""

from html.parser import HTMLParser
from typing import Any

from app.domain.inbound_gate import Refusal
from app.domain.integration import EventOutcome
from app.domain.webhook_ledger import AddressKey, WebhookLedger
from tests.admin.conftest import Site
from tests.support.admin_site import site_app
from tests.support.contact_center_site import (
    PAGE,
    SUPPLIER,
    _call_from,
    _connect,
    _set_settings,
)

FORGED = "x" * 43


class _Handling:
    """Stands in for the integration service: every verified event comes to ``outcome``."""

    def __init__(self, outcome: EventOutcome) -> None:
        self.outcome = outcome

    async def record_task_event(self, *args: object, **kwargs: object) -> EventOutcome:
        return self.outcome


class _Tables(HTMLParser):
    """Reads the tables of a page as structure: caption, column and row header cells."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict[str, Any]] = []
        self._open: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables.append({"caption": "", "columns": [], "rows": []})
        elif tag == "caption":
            self._open = "caption"
        elif tag == "th" and self.tables:
            scope = dict(attrs).get("scope")
            self._open = {"col": "columns", "row": "rows"}.get(scope or "", "other")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("caption", "th"):
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._open in ("caption", "columns", "rows") and data.strip():
            cell = self.tables[-1]
            if self._open == "caption":
                cell["caption"] += data.strip()
            else:
                cell[self._open].append(data.strip())


def _card(page: str) -> str:
    return page.split('id="contact-center"', 1)[1].split("</section>", 1)[0]


async def test_the_counts_are_a_real_table_with_a_caption_and_header_cells(site: Site) -> None:
    await _connect(site)
    site_app(site).state.integration_service = _Handling(EventOutcome.RECORDED)
    await _call_from(site, SUPPLIER)
    await _call_from(site, SUPPLIER)
    await _call_from(site, SUPPLIER, secret=FORGED)

    parser = _Tables()
    parser.feed(_card((await site.client.get(PAGE)).text))

    (counts,) = parser.tables
    assert counts["caption"] == "Aanroepen in dit venster per uitkomst"
    assert counts["columns"] == ["Uitkomst", "Aanroepen"]
    assert counts["rows"] == ["Geaccepteerd en verwerkt", "Geweigerd: handtekening klopt niet"]


async def test_an_address_the_ledger_could_not_record_shows_the_empty_state(site: Site) -> None:
    """With the ledger full of live, alerted entries a new address has no view at all."""
    await _connect(site)
    site_app(site).state.integration_service = _Handling(EventOutcome.RECORDED)
    _set_settings(site, security_event_max_sources=10)
    ledger: WebhookLedger = site_app(site).state.webhook_ledger
    for index in range(10):
        for _ in range(25):  # past the default threshold: each entry has alerted
            ledger.refused(AddressKey("wxcc", f"other{index}"), Refusal.STALE)

    answered = await _call_from(site, SUPPLIER)  # answered as always, but not recorded

    assert answered.status_code == 204
    assert ledger.view(AddressKey("wxcc", "demo")) is None
    card = (await site.client.get(PAGE)).text
    assert card.count("Geen gezien sinds dit proces startte") == 2
    assert "Geen aanroepen in dit venster." in card

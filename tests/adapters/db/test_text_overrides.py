"""The cached organisation-text source behind the translator (ADR 0004 §9, ADR 0074)."""

from app.adapters.db.text_overrides import REFRESH_SECONDS, CachedTextOverrides
from app.i18n import Translator
from tests.fakes.persistence import InMemoryUnitOfWork
from tests.support.admin import two_organisations


class Ticker:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _source(uow: InMemoryUnitOfWork, ticker: Ticker) -> CachedTextOverrides:
    return CachedTextOverrides(uow, ticker)


async def test_nothing_is_known_before_the_first_refresh_and_global_is_never_set() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[1] = {("nl", "booked.text"): "Eigen"}
    source = _source(uow, Ticker())

    assert source.get("nl", "booked.text", 1) is None

    await source.refresh()

    assert source.get("nl", "booked.text", 1) == "Eigen"
    assert source.get("nl", "booked.text", 2) is None  # another organisation
    assert source.get("en", "booked.text", 1) is None  # another locale
    assert source.get("nl", "booked.text", None) is None  # no global overrides yet


async def test_refresh_reads_the_database_at_most_every_interval() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    ticker = Ticker()
    source = _source(uow, ticker)
    await source.refresh()
    uow.state.organisation_texts[1] = {("nl", "booked.text"): "Nieuw"}

    ticker.now += REFRESH_SECONDS - 0.1
    await source.refresh()
    assert source.get("nl", "booked.text", 1) is None

    ticker.now += 0.2
    await source.refresh()
    assert source.get("nl", "booked.text", 1) == "Nieuw"


async def test_invalidate_makes_the_next_refresh_read_at_once() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    source = _source(uow, Ticker())
    await source.refresh()
    uow.state.organisation_texts[1] = {("nl", "booked.text"): "Nieuw"}

    source.invalidate()
    await source.refresh()

    assert source.get("nl", "booked.text", 1) == "Nieuw"


async def test_a_removed_text_disappears_after_a_refresh() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[1] = {("nl", "booked.text"): "Eigen"}
    source = _source(uow, Ticker())
    await source.refresh()
    uow.state.organisation_texts[1] = {}

    source.invalidate()
    await source.refresh()

    assert source.get("nl", "booked.text", 1) is None


async def test_the_translator_prefers_the_organisation_text_over_the_catalogue() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[1] = {("nl", "greeting"): "Welkom {name}"}
    source = _source(uow, Ticker())
    translator = Translator({"nl": {"greeting": "Hallo {name}"}}, source)

    await translator.refresh()

    assert translator.gettext("nl", "greeting", organisation_id=1, name="Ada") == "Welkom Ada"
    assert translator.gettext("nl", "greeting", organisation_id=2, name="Ada") == "Hallo Ada"
    assert translator.gettext("nl", "greeting", name="Ada") == "Hallo Ada"


async def test_without_a_database_the_shipped_texts_apply() -> None:
    source = CachedTextOverrides()

    await source.refresh()

    assert source.get("nl", "booked.text", 1) is None


async def test_attaching_the_database_loads_it_on_the_next_refresh() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[1] = {("nl", "booked.text"): "Eigen"}
    source = CachedTextOverrides()

    source.attach(uow)
    await source.refresh()

    assert source.get("nl", "booked.text", 1) == "Eigen"


class BrokenUnitOfWork(InMemoryUnitOfWork):
    """A database whose texts cannot be read (for example a table that is not migrated yet)."""

    def read(self):  # type: ignore[no-untyped-def]
        raise RuntimeError("no such table")


async def test_a_failed_read_keeps_the_last_copy_and_retries_after_the_interval() -> None:
    uow = InMemoryUnitOfWork(two_organisations())
    uow.state.organisation_texts[1] = {("nl", "booked.text"): "Eigen"}
    ticker = Ticker()
    source = CachedTextOverrides(uow, ticker)
    await source.refresh()

    source.attach(BrokenUnitOfWork(two_organisations()))
    await source.refresh()  # does not raise
    assert source.get("nl", "booked.text", 1) == "Eigen"

    source.attach(uow)
    ticker.now += REFRESH_SECONDS
    await source.refresh()
    assert source.get("nl", "booked.text", 1) == "Eigen"


async def test_a_database_that_cannot_be_read_leaves_the_shipped_texts() -> None:
    source = CachedTextOverrides(BrokenUnitOfWork(two_organisations()))

    await source.refresh()

    assert source.get("nl", "booked.text", 1) is None

"""A database fault while booking rolls everything back and fails closed (ASVS V16.5.2, V16.5.3).

The fault is real: a trigger makes SQLite refuse the insert of the booking, after the slot was
already written inside the same transaction. Whatever the page does next, nothing may remain
of the half-made booking, and no management link may be handed out.
"""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from structlog.testing import capture_logs

from app.config import Settings
from app.domain.audit import AuditEvent
from app.main import create_app
from tests.api.test_security_events import ADA, FRIDAY_0900, SAME_ORIGIN
from tests.fakes.clock import FixedClock
from tests.support.admin import SUPER
from tests.support.domain import NOW

COUNTS = {
    "slots": "SELECT COUNT(*) FROM slots",
    "bookings": "SELECT COUNT(*) FROM bookings",
    "jobs": "SELECT COUNT(*) FROM jobs",
}
FAULT = "CREATE TRIGGER fault BEFORE INSERT ON bookings BEGIN SELECT RAISE(ABORT, 'disk full'); END"
EMAIL = ADA["email"]


@dataclass
class Faulty:
    """The app, its database file, and the way to break and repair the booking insert."""

    client: httpx.AsyncClient
    path: str
    app: object

    def _run(self, sql: str) -> None:
        with closing(sqlite3.connect(self.path)) as database:
            database.execute(sql)
            database.commit()

    def break_booking_insert(self) -> None:
        self._run(FAULT)

    def repair(self) -> None:
        self._run("DROP TRIGGER fault")

    def rows(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.path)) as database:
            return {t: database.execute(sql).fetchone()[0] for t, sql in COUNTS.items()}

    async def events(self) -> list[AuditEvent]:
        log = await self.app.state.admin_service.audit_log(SUPER)  # type: ignore[attr-defined]
        return [entry.event for entry in log]

    async def book(self) -> httpx.Response:
        return await self.client.post(
            "/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=SAME_ORIGIN
        )


@pytest.fixture
async def faulty(demo_settings: Settings, database_url: str) -> AsyncIterator[Faulty]:
    app = create_app(demo_settings, FixedClock(NOW))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=("203.0.113.7", 1))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield Faulty(client, database_url.removeprefix("sqlite+aiosqlite:///"), app)


async def test_a_database_fault_rolls_the_whole_booking_back(faulty: Faulty) -> None:
    before = faulty.rows()
    faulty.break_booking_insert()

    response = await faulty.book()

    assert response.status_code == 500
    assert faulty.rows() == before  # the slot written before the failing insert is gone too


async def test_a_database_fault_hands_out_nothing_the_participant_could_use(
    faulty: Faulty,
) -> None:
    faulty.break_booking_insert()

    response = await faulty.book()

    assert "/b/" not in response.text  # no management link
    assert "location" not in response.headers  # no redirect to a confirmation page
    assert not any(value in response.text for value in (EMAIL, "Lovelace", "disk full"))
    assert response.headers["content-type"].startswith("text/plain")  # generic, no internals


async def test_the_slot_stays_bookable_after_the_fault_is_gone(faulty: Faulty) -> None:
    faulty.break_booking_insert()
    await faulty.book()
    faulty.repair()

    retry = await faulty.book()

    assert retry.status_code < 400
    assert faulty.rows()["bookings"] == 1


async def test_a_database_fault_is_not_mistaken_for_an_attack(faulty: Faulty) -> None:
    faulty.break_booking_insert()

    await faulty.book()

    assert not [e for e in await faulty.events() if e.action == "request.rejected"]


@pytest.mark.xfail(
    strict=True,
    reason="T10 finding: a database fault while booking leaves no event with a code "
    "(V16.3.3); the 500 is only a line in the web server's log.",
)
async def test_a_database_fault_while_booking_leaves_an_event_with_a_code(faulty: Faulty) -> None:
    faulty.break_booking_insert()

    await faulty.book()

    events = await faulty.events()
    assert events
    assert all(e.reason.isidentifier() for e in events)


@pytest.mark.xfail(
    strict=True,
    reason="T10 finding: the exception that leaves the transaction carries the booking's "
    "first name, last name and email in its message (SQLAlchemy's [parameters: ...]), so the "
    "web server's traceback log holds personal data (CLAUDE.md: no PII in exceptions; V16.5.1).",
)
async def test_the_exception_of_a_database_fault_carries_no_personal_data(
    demo_settings: Settings, faulty: Faulty
) -> None:
    faulty.break_booking_insert()
    app = faulty.app
    transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 2))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        with pytest.raises(IntegrityError) as raised:
            await client.post(
                "/g/demo/book", data={"start": FRIDAY_0900, **ADA}, headers=SAME_ORIGIN
            )

    text = str(raised.value)
    assert not any(value in text for value in (EMAIL, ADA["first_name"], ADA["last_name"]))


async def test_a_failing_security_log_does_not_turn_a_refusal_into_a_server_error(
    faulty: Faulty, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> bool:
        raise OSError("audit table unavailable")

    monkeypatch.setattr(faulty.app.state.security_log, "record", broken)  # type: ignore[attr-defined]
    before = faulty.rows()

    with capture_logs() as logs:
        response = await faulty.client.post(
            "/g/demo/book",
            data={"start": FRIDAY_0900, **ADA, "website": "https://spam.example"},
            headers=SAME_ORIGIN,
        )

    assert response.status_code == 400  # the bot is still refused, and nothing is booked
    assert faulty.rows() == before
    assert [e["error"] for e in logs if e["event"] == "security_log.failed"] == ["OSError"]

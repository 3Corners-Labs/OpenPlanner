"""The upgrade fixture: a database a released version wrote, and the checks run on it (ADR 0116).

``seed_upgrade_fixture`` fills a fresh database with generated data of every kind the schema
holds: the demo organisations and groups with their closures, bookings in every status (one
purged), pending jobs, audit events, a stored value for every class C and D setting, an
organisation identity provider, an API client and the version that wrote it. All personal data
is made up (``example.org`` addresses); the release job runs it in the released image and
attaches the file to the release, and the upgrade matrix (``scripts/upgrade-matrix.sh``) takes
it through ``alembic upgrade head``, a downgrade and the released image again.

``inspect_fixture`` reads what the matrix compares before and after a step: rows per table,
bookings with and without personal data, pending jobs and whether the worker can claim them,
and whether every stored setting is still within its bounds. ``compare_states`` turns two
readings into findings. Development and CI only; the CLI refuses the seed in production.
"""

import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import column, func, insert, inspect, select, table
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import ColumnElement

from app.adapters.db.backup import backup_database, database_path
from app.adapters.db.engine import create_engine
from app.adapters.db.jobs import SqlJobQueue
from app.adapters.db.models import BookingRow, JobRow, SlotRow
from app.adapters.db.seed import (
    DEMO_CAPACITY,
    DEMO_SLUG,
    OTHER_ORGANISATION_SLUG,
    local_time,
    seed_closure_examples,
    seed_demo,
    seeded_group,
    store_example_booking,
)
from app.adapters.db.uow import SqlUnitOfWork
from app.domain.api_clients import ApiClient, credential_hash, new_client_id
from app.domain.audit import AuditEvent, Outcome, Source
from app.domain.authorization import ApiScope, Scope
from app.domain.booking import BookingStatus
from app.domain.channels import ChannelKind
from app.domain.identity_providers import ClientAuth, OrganisationProvider
from app.domain.job_plan import JobType, PlannedJob
from app.domain.platform_settings import usable
from app.domain.ports import Transaction
from app.domain.privacy import PURGED_FIELDS
from app.domain.settings_registry import REGISTRY, Kind, SettingClass, lookup
from app.domain.tokens import hash_token, new_token_salt
from app.domain.updates import RunningVersion

_ALEMBIC = "alembic_version"
"""Alembic's own table: holds the revision, not counted with the data."""
FIXTURE_PROVIDER_KEY = "fixture-idp"
"""Key of the organisation identity provider in the fixture (disabled, no secret)."""
_BULK_BATCH = 2_000
"""Rows per insert of the large fixture: big enough to be fast, small enough for SQLite."""
_BULK_PURGED_SHARE = 2
"""One in this many generated past bookings has its personal data deleted, as on a live host."""
_PAST_STATUSES = (BookingStatus.COMPLETED, BookingStatus.NO_SHOW, BookingStatus.CANCELLED)
_BULK_OFFSET = timedelta(minutes=7)
_BULK_FUTURE_SHARE = 10
"""One in this many generated bookings lies in the future (confirmed); the rest are past."""


@dataclass(frozen=True)
class FixtureState:
    """What the upgrade matrix compares before and after a schema step.

    Attributes:
        revision: The Alembic revision the database is at (empty before any migration).
        rows: Rows per table, ``alembic_version`` left out.
        live_with_data: Bookings not purged that hold their personal data.
        live_without_data: Bookings not purged with an empty name or email (should be 0).
        purged: Bookings whose personal data was deleted.
        purged_with_data: Purged bookings that still hold a personal field (should be 0).
        pending_jobs: Jobs waiting for the worker.
        claimable_jobs: Of those, how many the worker's claim takes on a copy of the file.
        settings_unusable: Stored class C and D keys the current code would not apply.
        settings_unknown: Stored keys the current code does not know (renamed or dropped).
    """

    revision: str
    rows: dict[str, int]
    live_with_data: int
    live_without_data: int
    purged: int
    purged_with_data: int
    pending_jobs: int
    claimable_jobs: int
    settings_unusable: list[str] = field(default_factory=list)
    settings_unknown: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """Return the state as a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "FixtureState":
        """Read a state written by ``to_json``."""
        return cls(**data)


async def seed_upgrade_fixture(
    engine: AsyncEngine,
    now: datetime,
    token_key: bytes,
    *,
    version: str,
    commit: str,
    bookings: int = 0,
) -> None:
    """Fill a migrated, empty database with the generated data of the upgrade fixture.

    Args:
        engine: A database at ``alembic upgrade head`` of the running code.
        now: The moment the fixture is made; bookings lie before and after it.
        token_key: The key that derives management links (ADR 0011).
        version: ``APP_VERSION`` of the image that writes the fixture.
        commit: ``APP_COMMIT`` of that image.
        bookings: Extra generated bookings for the duration budget (0 for the release fixture).
    """
    await seed_demo(engine)
    await seed_closure_examples(engine, now, token_key)
    uow = SqlUnitOfWork(engine)
    async with uow.locked() as tx:
        await _bookings_in_every_status(tx, now, token_key)
        await _settings_of_every_class(tx, now)
        await _integrations(tx, now)
        await _audit_events(tx, now)
        await tx.add_running_version(RunningVersion(version, commit, now, None))
    if bookings:
        await _bulk_bookings(engine, now, bookings)


async def _bookings_in_every_status(tx: Transaction, now: datetime, token_key: bytes) -> None:
    """Store a booking of the demo group in every status, with jobs, and purge a past one."""
    group = await seeded_group(tx, DEMO_SLUG)
    for days, status in enumerate(BookingStatus, start=1):
        past = status in _PAST_STATUSES
        start = local_time(group, now, -days if past else days + 1, time(9, 30))
        _, booking = await store_example_booking(tx, group, start, now, token_key, status=status)
        if booking.id is None:
            raise ValueError("the booking must be stored")
        if not past:
            await tx.add_jobs(
                booking.id,
                [
                    PlannedJob(JobType.SEND_REMINDER, start - timedelta(hours=24), start),
                    PlannedJob(JobType.PURGE_PII, start + timedelta(hours=24), None),
                ],
            )
    start = local_time(group, now, -30, time(10))
    _, purged = await store_example_booking(
        tx, group, start, now, token_key, status=BookingStatus.COMPLETED
    )
    await tx.purge_personal_data(purged.id or 0, start + timedelta(hours=24))


async def _settings_of_every_class(tx: Transaction, now: datetime) -> None:
    """Store the default of every class C and D setting that the portal holds as a value.

    Secrets are left out: they are sealed under the writer's key, which the matrix does not
    have. Class A and B live in the environment, not in the database.
    """
    organisations = [
        o.id
        for o in await tx.list_organisations(Scope(everything=True))
        if o.slug in (DEMO_SLUG, OTHER_ORGANISATION_SLUG)
    ]
    for spec in REGISTRY:
        if spec.kind is Kind.SECRET or spec.default is None or not spec.in_portal:
            continue
        if spec.class_ is SettingClass.PLATFORM:
            await tx.save_platform_setting(spec.key, spec.default, now, None)
        else:
            for organisation_id in organisations:
                await tx.save_organisation_setting(organisation_id, spec.key, spec.default, now)
    await tx.bump_settings_version()


async def _integrations(tx: Transaction, now: datetime) -> None:
    """Store a disabled identity provider and an API client of the demo organisation."""
    await tx.add_identity_provider(
        OrganisationProvider(
            id=None,
            organisation=DEMO_SLUG,
            key=FIXTURE_PROVIDER_KEY,
            name="Fixture IdP",
            discovery_url="https://idp.example.org/.well-known/openid-configuration",
            issuer="https://idp.example.org",
            client_id="openplanner",
            client_auth=ClientAuth.CLIENT_SECRET_BASIC,
            scopes="openid profile email",
            entitlement_claim="openplanner_entitlements",
            entitlement_prefix="openplanner",
            enabled=False,
            secret_set=False,
            created_at=now,
            updated_at=now,
            checked_at=None,
        ),
        None,
    )
    await tx.add_api_client(
        ApiClient(
            id=None,
            client_id=new_client_id(),
            name="Fixture bot",
            organisation=DEMO_SLUG,
            groups=frozenset(),
            scopes=frozenset({ApiScope.AVAILABILITY_READ}),
            networks=("198.51.100.0/24",),
            enabled=True,
            secret_hash=credential_hash(new_token_salt()),
            secret_expires_at=now + timedelta(days=90),
            created_at=now,
        )
    )


async def _audit_events(tx: Transaction, now: datetime) -> None:
    """Store an admin and a system audit event, as a running installation writes them."""
    await tx.add_audit_event(
        AuditEvent(now, "fixture-admin", "group.view", Outcome.SUCCESS, organisation=DEMO_SLUG)
    )
    await tx.add_audit_event(
        AuditEvent(now, "system", "booking.purged", Outcome.SUCCESS, source=Source.SYSTEM)
    )


async def _bulk_bookings(engine: AsyncEngine, now: datetime, count: int) -> None:
    """Insert ``count`` generated bookings of the demo group, two per slot, in batches.

    Past bookings are completed, no-shows or cancellations, half of them purged; the last tenth
    lies in the future and is confirmed. Every row has its own token hash and public reference.
    """
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        group_id = (await seeded_group(tx, DEMO_SLUG)).id
    span = timedelta(minutes=30 * (count // DEMO_CAPACITY + 1))
    # Seven minutes off the half hour, so no generated slot meets a seeded one.
    start = now.replace(second=0, microsecond=0) - span + span / _BULK_FUTURE_SHARE
    base = start.replace(minute=start.minute // 30 * 30) + _BULK_OFFSET
    async with engine.begin() as connection:
        for first in range(0, count, _BULK_BATCH):
            numbers = range(first, min(first + _BULK_BATCH, count))
            slots = [
                {
                    "group_id": group_id,
                    "start": _bulk_start(base, n),
                    "end": _bulk_start(base, n) + timedelta(minutes=30),
                    "capacity_snapshot": DEMO_CAPACITY,
                    "kind": "scheduled",
                }
                for n in numbers
                if n % DEMO_CAPACITY == 0
            ]
            if slots:
                await connection.execute(insert(SlotRow), slots)
            ids = await _slot_ids(connection, group_id, base, numbers)
            await connection.execute(
                insert(BookingRow), [_bulk_booking(group_id, ids, base, now, n) for n in numbers]
            )


def _bulk_start(base: datetime, n: int) -> datetime:
    """Return the slot start of generated booking ``n``: half-hour steps from ``base``."""
    return base + timedelta(minutes=30 * (n // DEMO_CAPACITY))


async def _slot_ids(
    connection: AsyncConnection, group_id: int, base: datetime, numbers: range
) -> dict[datetime, int]:
    """Return the ids of the generated slots of ``numbers`` by start."""
    starts = sorted({_bulk_start(base, n) for n in numbers})
    rows = await connection.execute(
        select(SlotRow.start, SlotRow.id).where(
            SlotRow.group_id == group_id,
            SlotRow.kind == "scheduled",
            SlotRow.start.between(starts[0], starts[-1]),
        )
    )
    return {start: slot_id for start, slot_id in rows.all()}


def _bulk_booking(
    group_id: int, slot_ids: dict[datetime, int], base: datetime, now: datetime, n: int
) -> dict[str, object]:
    """Return the row of generated booking ``n`` with made-up personal data."""
    start = _bulk_start(base, n)
    future = start > now
    status = BookingStatus.CONFIRMED if future else _PAST_STATUSES[n % len(_PAST_STATUSES)]
    purged = not future and n % _BULK_PURGED_SHARE == 0
    salt = new_token_salt()
    return {
        "slot_id": slot_ids[start],
        "group_id": group_id,
        "status": status.value,
        "locale": "nl",
        "consents": "",
        "management_token_hash": hash_token(salt),
        "management_token_salt": salt,
        "public_ref": f"fx{n:010d}",
        "created_at": start - timedelta(days=3),
        "cancelled_at": start - timedelta(days=1) if status is BookingStatus.CANCELLED else None,
        "pii_purged_at": start + timedelta(hours=24) if purged else None,
        # Every row carries the same keys: an executemany insert takes them from the first.
        "first_name": None if purged else "Voorbeeld",
        "last_name": None if purged else f"Deelnemer {n}",
        "email": None if purged else f"deelnemer-{n}@example.org",
    }


async def inspect_fixture(database_url: str, now: datetime) -> FixtureState:
    """Read the state of the SQLite database at ``database_url`` for the upgrade matrix.

    The claim of pending jobs runs on a copy, so the file itself is not changed.

    Args:
        database_url: ``DATABASE_URL`` of an existing SQLite file.
        now: The moment to claim at; pass a time after every job's ``run_at``.

    Raises:
        FileNotFoundError: The file does not exist (SQLite would create an empty one).
    """
    path = database_path(database_url)
    if not path.is_file():
        raise FileNotFoundError(path)
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            names = await connection.run_sync(_table_names)
            rows = {name: await _count(connection, name) for name in names if name != _ALEMBIC}
            revision = (
                await connection.scalar(select(table(_ALEMBIC, column("version_num"))))
                if _ALEMBIC in names
                else ""
            ) or ""
            privacy = await _privacy_counts(connection)
            pending = await connection.scalar(
                select(func.count()).select_from(JobRow).where(JobRow.status == "pending")
            )
        unusable, unknown = await _settings_findings(engine)
    finally:
        await engine.dispose()
    return FixtureState(
        revision=str(revision),
        rows=rows,
        **privacy,
        pending_jobs=int(pending or 0),
        claimable_jobs=await _claimable(database_url, now),
        settings_unusable=unusable,
        settings_unknown=unknown,
    )


def compare_states(before: FixtureState, after: FixtureState) -> list[str]:
    """Return what the step from ``before`` to ``after`` broke; empty when nothing did.

    A table may gain rows or appear; it may not lose rows or disappear unless it was empty
    (a contract step drops only what the previous minor stopped using). Personal data must be
    present on every live booking and absent on every purged one, every pending job must stay
    claimable, and every stored setting must stay within its bounds.
    """
    findings = [
        f"table {name}: {count} rows before, {after.rows.get(name, 0)} after"
        for name, count in before.rows.items()
        if count and after.rows.get(name, 0) < count
    ]
    if after.live_with_data != before.live_with_data:
        findings.append(
            f"live bookings with personal data: {before.live_with_data} before, "
            f"{after.live_with_data} after"
        )
    if after.purged != before.purged:
        findings.append(f"purged bookings: {before.purged} before, {after.purged} after")
    findings += _state_findings(after)
    return findings


def _state_findings(state: FixtureState) -> list[str]:
    """Return the invariants one state breaks on its own."""
    findings = []
    if state.live_without_data:
        findings.append(f"{state.live_without_data} live bookings lost their personal data")
    if state.purged_with_data:
        findings.append(f"{state.purged_with_data} purged bookings hold personal data")
    if state.claimable_jobs != state.pending_jobs:
        findings.append(
            f"{state.pending_jobs} pending jobs, but the worker claims {state.claimable_jobs}"
        )
    findings += [
        f"setting {key} holds a value the code would not apply" for key in state.settings_unusable
    ]
    return findings


def _table_names(connection: Connection) -> list[str]:
    """Return every table name of the database."""
    return sorted(inspect(connection).get_table_names())


async def _count(connection: AsyncConnection, name: str) -> int:
    """Return the number of rows of table ``name``."""
    return int(await connection.scalar(select(func.count()).select_from(table(name))) or 0)


async def _privacy_counts(connection: AsyncConnection) -> dict[str, int]:
    """Count bookings by whether they are purged and whether they hold personal data."""
    live = BookingRow.pii_purged_at.is_(None)
    has_data = BookingRow.first_name.is_not(None) & BookingRow.email.is_not(None)
    any_data = func.coalesce(*(getattr(BookingRow, name) for name in PURGED_FIELDS))

    async def count(*where: ColumnElement[bool]) -> int:
        query = select(func.count()).select_from(BookingRow).where(*where)
        return int(await connection.scalar(query) or 0)

    return {
        "live_with_data": await count(live, has_data),
        "live_without_data": await count(live, ~has_data),
        "purged": await count(~live),
        "purged_with_data": await count(~live, any_data.is_not(None)),
    }


async def _settings_findings(engine: AsyncEngine) -> tuple[list[str], list[str]]:
    """Return the stored settings keys the code would not apply, and those it does not know.

    "Would not apply" is ``platform_settings.usable``, the rule the running code uses.
    """
    uow = SqlUnitOfWork(engine)
    async with uow.read() as tx:
        stored = [await tx.platform_settings()]
        stored += list((await tx.organisation_settings()).values())
    unusable: set[str] = set()
    unknown: set[str] = set()
    for values in stored:
        for key, value in values.items():
            spec = lookup(key)
            if spec is None:
                unknown.add(key)
            elif not usable(spec, value):
                unusable.add(key)
    return sorted(unusable), sorted(unknown)


async def _claimable(database_url: str, now: datetime) -> int:
    """Claim every due job on a copy of the database as the worker would; return how many."""
    with tempfile.TemporaryDirectory() as directory:
        copy = backup_database(database_path(database_url), Path(directory), "claim")
        engine = create_engine(make_url(database_url).set(database=str(copy)).render_as_string())
        try:
            claimed = await SqlJobQueue(engine).claim_due(
                now, list(JobType), list(ChannelKind), limit=1_000_000
            )
        finally:
            await engine.dispose()
    return len(claimed)

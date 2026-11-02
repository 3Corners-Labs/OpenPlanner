"""The *Update back-ups* part of System status (ADR 0102 point 10)."""

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.adapters.db.backup import backup_directory
from app.domain.backup_age import BACKUP_GRACE, BACKUP_LIFETIME
from tests.admin.conftest import AUDITOR, SUPER, Site


def _directory(site: Site) -> Path:
    app: Any = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    return backup_directory(app.state.settings.database_url)


def _backup(site: Site, name: str, age: timedelta) -> None:
    directory = _directory(site)
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.db"
    path.write_bytes(b"x")
    written = (site.clock.now() - age).timestamp()
    os.utime(path, (written, written))


async def _part(site: Site) -> str:
    text = (await site.client.get("/admin/settings")).text
    return text.split('id="backups-heading"')[1].split('id="address-heading"')[0]


async def test_without_a_backup_the_part_says_so(site: Site) -> None:
    await site.login_as(AUDITOR)

    part = await _part(site)

    assert "Geen back-ups aanwezig" in part
    assert "chip--warning" not in part


async def test_a_recent_backup_shows_its_age_without_a_warning(site: Site) -> None:
    _backup(site, "request-1", timedelta(hours=3))
    _backup(site, "request-2", timedelta(hours=1))
    await site.login_as(SUPER)

    part = await _part(site)

    assert "Aantal back-ups" in part
    assert "3 uur oud" in part
    assert "chip--warning" not in part
    assert 'role="alert"' not in part


async def test_a_backup_past_its_lifetime_plus_grace_warns(site: Site) -> None:
    _backup(site, "request-1", BACKUP_LIFETIME + BACKUP_GRACE + timedelta(hours=1))
    await site.login_as(SUPER)

    part = await _part(site)

    assert 'role="alert"' in part
    assert "chip--warning" in part
    assert "27 uur oud" in part


async def test_the_english_page_uses_english_words(site: Site) -> None:
    _backup(site, "request-1", timedelta(hours=30))
    await site.login_as(SUPER)

    text = (await site.client.get("/admin/settings?lang=en")).text
    part = text.split('id="backups-heading"')[1].split('id="address-heading"')[0]

    assert "Update back-ups" in text
    assert "30 h old" in part

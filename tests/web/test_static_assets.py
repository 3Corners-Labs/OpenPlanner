"""Versioned static asset URLs: a browser never keeps an old stylesheet (backlog step 6b).

After an update a browser held on to the ``admin.css`` of the release before, so the new
markup met old rules. Every ``/static/`` link now carries the content hash of its file, and
a versioned URL may be cached for good because its content can never change.
"""

import hashlib
import os
import re
from pathlib import Path

import pytest

from app.web.static_assets import ASSETS, STATIC_DIR, StaticAssets
from tests.admin.conftest import SUPER, Site

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

LITERAL_STATIC_LINK = re.compile(r"""["']/static/""")
"""A template that writes a ``/static/`` URL by hand instead of calling ``static_url``."""

VERSIONED = re.compile(r'(?:href|src)="(/static/[^"?]+\?v=[0-9a-f]{12})"')


def _write(root: Path, name: str, content: bytes) -> None:
    (root / name).write_bytes(content)


def test_the_url_carries_the_content_hash(tmp_path: Path) -> None:
    _write(tmp_path, "a.css", b"body{}")

    url = StaticAssets(tmp_path).url("a.css")

    digest = hashlib.sha256(b"body{}").hexdigest()[:12]
    assert url == f"/static/a.css?v={digest}"


def test_the_version_changes_when_the_file_changes(tmp_path: Path) -> None:
    _write(tmp_path, "a.css", b"body{}")
    assets = StaticAssets(tmp_path)
    before = assets.url("a.css")

    _write(tmp_path, "a.css", b"body{x}")  # a different size
    after_size = assets.url("a.css")
    _write(tmp_path, "a.css", b"body{y}")  # the same size, a later time
    stat = (tmp_path / "a.css").stat()
    os.utime(tmp_path / "a.css", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    after_content = assets.url("a.css")

    assert len({before, after_size, after_content}) == 3


def test_a_file_that_did_not_change_is_not_read_again(tmp_path: Path) -> None:
    _write(tmp_path, "a.css", b"body{}")
    assets = StaticAssets(tmp_path)
    first = assets.url("a.css")
    (tmp_path / "a.css").chmod(0)  # unreadable now: only a cached digest can still answer

    try:
        assert assets.url("a.css") == first
    finally:
        (tmp_path / "a.css").chmod(0o600)


@pytest.mark.parametrize("name", ["missing.css", "../secret.txt", "/etc/passwd", "sub/../../x"])
def test_a_missing_file_or_a_path_outside_the_folder_is_an_error(tmp_path: Path, name: str) -> None:
    (tmp_path.parent / "secret.txt").write_text("x")

    with pytest.raises(FileNotFoundError):
        StaticAssets(tmp_path).url(name)


def test_the_real_assets_have_a_version() -> None:
    assert re.fullmatch(r"/static/brand/mark\.svg\?v=[0-9a-f]{12}", ASSETS.url("brand/mark.svg"))


def test_no_template_writes_a_static_url_by_hand() -> None:
    offenders = [
        str(path)
        for path in sorted(Path("app").rglob("*.html"))
        if LITERAL_STATIC_LINK.search(path.read_text())
    ]

    assert offenders == []


def test_the_check_finds_a_hand_written_link() -> None:
    assert LITERAL_STATIC_LINK.search('<link rel="stylesheet" href="/static/admin.css">')
    assert not LITERAL_STATIC_LINK.search("""<link href="{{ static_url('admin.css') }}">""")


async def test_every_asset_link_of_a_page_is_versioned_and_served_for_good(site: Site) -> None:
    await site.login_as(SUPER)

    page = (await site.client.get("/admin/")).text

    links = VERSIONED.findall(page)
    assert "/static/admin.css" in {link.split("?")[0] for link in links}
    assert len(links) == len(re.findall(r'(?:href|src)="/static/', page))
    for link in links:
        response = await site.client.get(link)
        assert response.status_code == 200
        assert response.headers["cache-control"] == IMMUTABLE


async def test_an_unversioned_or_stale_url_is_revalidated_not_kept(site: Site) -> None:
    for url in ("/static/admin.css", "/static/admin.css?v=000000000000", "/static/admin.css?v=x"):
        response = await site.client.get(url)

        assert response.status_code == 200
        assert response.headers["cache-control"] == REVALIDATE
        assert "etag" in response.headers


async def test_the_current_version_is_the_hash_of_the_served_bytes(site: Site) -> None:
    url = ASSETS.url("site.css")

    response = await site.client.get(url)

    digest = hashlib.sha256(response.content).hexdigest()[:12]
    assert url.endswith(f"?v={digest}")
    assert (STATIC_DIR / "site.css").read_bytes() == response.content


async def test_a_missing_file_stays_a_404_without_a_cache_header(site: Site) -> None:
    response = await site.client.get("/static/nope.css?v=000000000000")

    assert response.status_code == 404
    assert "immutable" not in response.headers.get("cache-control", "")

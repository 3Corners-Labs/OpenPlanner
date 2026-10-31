"""Versioned URLs for the files under ``/static``, and the cache rules that go with them.

After an update a browser kept the ``admin.css`` of the release before, so new markup met old
rules. Every template links a static file through ``static_url``, which appends the first
characters of the file's SHA-256 (``/static/admin.css?v=3f9a1c07b2de``). The URL changes exactly
when the file does, so a response for the current version may be cached for good. Any other
request (no version, an old version) is answered with the current file and must be revalidated.
"""

import hashlib
from pathlib import Path
from urllib.parse import parse_qs

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIR = Path(__file__).parent / "static"
"""Stylesheets, scripts and images, served under ``/static``."""

STATIC_PREFIX = "/static"
"""Where ``STATIC_DIR`` is mounted."""

VERSION_PARAM = "v"
"""Query parameter that carries the version of a static file."""

DIGEST_LENGTH = 12
"""Hex characters of the SHA-256 kept as the version: 48 bits, ample to tell releases apart.
A fixed rule, not a setting: it is part of the URL format, and the full hash adds nothing."""

VERSIONED_CACHE = "public, max-age=31536000, immutable"
"""For a URL whose version is the file's current one: its content can never change under it,
so a year (the longest lifetime that browsers and proxies honour) is as good as forever."""

UNVERSIONED_CACHE = "no-cache"
"""Everything else is kept but revalidated with its ETag on each use."""


class StaticAssets:
    """The files of one static folder and their versions.

    A version is recomputed only when a file's size or modification time changes, so a request
    costs a ``stat`` and not a read.

    Attributes:
        root: The folder the files are served from.
    """

    def __init__(self, root: Path) -> None:
        """Remember ``root`` (resolved, so a link cannot lead out of it)."""
        self.root = root.resolve()
        self._seen: dict[str, tuple[tuple[int, int], str]] = {}

    def _file(self, name: str) -> Path:
        """Return the file ``name`` inside ``root``.

        Raises:
            FileNotFoundError: If it does not exist or lies outside ``root``.
        """
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise FileNotFoundError(name)
        return path

    def digest(self, name: str) -> str:
        """Return the version of the file ``name``: the start of its SHA-256.

        Raises:
            FileNotFoundError: If there is no such file under ``root``.
        """
        path = self._file(name)
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        known = self._seen.get(name)
        if known is not None and known[0] == stamp:
            return known[1]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:DIGEST_LENGTH]
        self._seen[name] = (stamp, digest)
        return digest

    def url(self, name: str) -> str:
        """Return the versioned URL of ``name`` (``/static/admin.css?v=3f9a1c07b2de``).

        Raises:
            FileNotFoundError: If there is no such file, so a mistyped link fails a test
                instead of shipping a broken page.
        """
        return f"{STATIC_PREFIX}/{name}?{VERSION_PARAM}={self.digest(name)}"


ASSETS = StaticAssets(STATIC_DIR)
"""The portal's own static files; the ``static_url`` of every template."""


class VersionedStaticFiles(StaticFiles):
    """``StaticFiles`` that caches a file for good under its current version only."""

    def __init__(self, assets: StaticAssets) -> None:
        """Serve the folder of ``assets``."""
        super().__init__(directory=assets.root)
        self._assets = assets

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Answer as ``StaticFiles`` does and add the cache rule for this URL."""
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["cache-control"] = (
                VERSIONED_CACHE if self._is_current(path, scope) else UNVERSIONED_CACHE
            )
        return response

    def _is_current(self, path: str, scope: Scope) -> bool:
        """Whether the request names the current version of ``path``."""
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        try:
            return query.get(VERSION_PARAM) == [self._assets.digest(path)]
        except FileNotFoundError:
            return False

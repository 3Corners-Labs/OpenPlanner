"""``ReleaseSource`` on the GitHub releases API (ADR 0029, 0059, 0067).

Lists the newest releases of one repository, releases and pre-releases alike (the channel is
applied by the domain); drafts are never listed for a read token. Of each release only the
tag, the release page, the publication time and the generated notes are used: the tag must be a
version (``v1.4.0``, ``v1.4.0-rc.2``) and the page must be this repository's release page, else
the release is ignored. The notes are reduced to plain text: the PR titles and numbers of
GitHub's generated notes, without authors or links.
The optional token is for a private repository and needs read access to its contents only.
"""

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import structlog

from app.adapters.webex_auth import http_failure
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.errors import DomainError
from app.domain.ports import Clock, IntegrationError, UnitOfWork
from app.domain.update_policy import (
    ANY_TIME,
    SupportLine,
    UpdateWindow,
    parse_support,
    parse_window,
)
from app.domain.update_service import UpdateService
from app.domain.updates import NOTES_CHARS, ListedRelease, UpdateChannel, Version

log = structlog.get_logger()

API_BASE = "https://api.github.com"
LISTED = 30
"""How many of the newest releases are looked at; candidates come with every merge."""
SUPPORT_FILE = "support.json"
"""The support index at the repository's root (ADR 0116 point 9)."""
SUPPORT_CHARS = 20_000
"""More than a support index ever needs; a larger answer is cut and then fails to parse."""

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_PULL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")
_BY = re.compile(r"\s+by\s+@[\w-]+")
_HEADING = re.compile(r"^#+\s*|\*\*")


class GitHubReleases:
    """Lists the releases of ``repository`` (``owner/name``).

    Args:
        http: Shared HTTP client.
        repository: ``owner/name`` on GitHub (validated by the settings).
        token: Optional read-only token for a private repository; never logged.
    """

    def __init__(self, http: httpx.AsyncClient, repository: str, token: str | None) -> None:
        """Store the collaborators."""
        self._http = http
        self._url = f"{API_BASE}/repos/{repository}/releases?per_page={LISTED}"
        self._latest_url = f"{API_BASE}/repos/{repository}/releases/latest"
        self._support_url = f"{API_BASE}/repos/{repository}/contents/{SUPPORT_FILE}"
        self._pages = f"https://github.com/{repository}/releases/"
        self._token = token

    async def listing(self) -> list[ListedRelease]:
        """Return the usable releases of the newest ``LISTED``; empty when there are none.

        Raises:
            IntegrationError: ``unauthorized``, ``rate_limited``, ``server_error``,
                ``rejected_<status>`` or ``unreachable``.
        """
        response = await self._get(self._url)
        if response is None:  # no release published yet
            return []
        data = response.json()
        if not isinstance(data, list):
            log.warning("updates.release_ignored", reason="not_a_list")
            return []
        found = [
            listed
            for item in data
            if isinstance(item, dict) and not item.get("draft")
            if (listed := self._release(item)) is not None
        ]
        if any(not item.version.is_candidate for item in found):
            return found
        # A candidate comes with every merge, so after LISTED of them the newest page holds no
        # release at all and an installation on the release channel would never see one.
        return [*found, *await self._latest_release()]

    async def support(self) -> tuple[SupportLine, ...] | None:
        """Return the lines of ``support.json`` on the default branch (ADR 0116 point 9).

        The release PR keeps it current (``scripts/support_index.py``) and every release
        carries a signed copy; the API serves the file itself, so no other host is contacted.
        ``None`` when the repository has none or it does not parse (logged, never fatal).

        Raises:
            IntegrationError: as ``listing`` does.
        """
        response = await self._get(self._support_url, accept="application/vnd.github.raw+json")
        if response is None:
            return None
        try:
            return tuple(parse_support(response.text[:SUPPORT_CHARS]))
        except ValueError:
            log.warning("updates.support_ignored", reason="not_a_support_index")
            return None

    async def _latest_release(self) -> list[ListedRelease]:
        """Return GitHub's latest published release (never a pre-release), if there is one."""
        response = await self._get(self._latest_url)
        data = response.json() if response is not None else None
        listed = self._release(data) if isinstance(data, dict) and not data.get("draft") else None
        return [listed] if listed is not None else []

    async def _get(
        self, url: str, accept: str = "application/vnd.github+json"
    ) -> httpx.Response | None:
        """Return the answer of ``url``; ``None`` for a 404 (nothing published).

        Raises:
            IntegrationError: as ``listing`` does.
        """
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = await self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("integration.unreachable", error=type(exc).__name__)
            raise IntegrationError("unreachable", retryable=True) from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise http_failure(response)
        return response

    def _release(self, data: dict[str, Any]) -> ListedRelease | None:
        """Return one release of the list, if it is a usable one."""
        tag, page = data.get("tag_name"), data.get("html_url")
        version = Version.parse(tag) if isinstance(tag, str) else None
        if version is None or not isinstance(page, str):
            log.warning("updates.release_ignored", reason="not_a_version")
            return None
        if not page.startswith(self._pages):
            log.warning("updates.release_ignored", reason="foreign_page")
            return None
        body = data.get("body")
        try:
            return ListedRelease(
                version,
                page,
                _moment(data.get("published_at")),
                plain_notes(body if isinstance(body, str) else ""),
            )
        except DomainError:
            log.warning("updates.release_ignored", reason="foreign_page")
            return None


def _moment(raw: object) -> datetime | None:
    """Parse GitHub's ``published_at`` (RFC 3339, ``Z``); ``None`` when absent or odd."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def plain_notes(body: str) -> str:
    """Reduce GitHub's generated notes to the PR titles and numbers, one per line.

    ``* feat(x): title by @user in https://github.com/o/r/pull/12`` becomes
    ``feat(x): title (#12)``; headings, the "Full Changelog" line, links and authors go.
    Anything else is kept as its plain text. At most ``NOTES_CHARS``.
    """
    lines = []
    for raw in body.splitlines():
        text = raw.strip()
        if not text or text.lower().startswith("**full changelog"):
            continue
        text = _HEADING.sub("", text).strip()
        if text.lower() == "what's changed":
            continue
        text = text.lstrip("*- ").strip()
        text = _PULL.sub(r"(#\1)", text)
        text = _BY.sub("", text).replace(" in (#", " (#")
        text = _LINK.sub(r"\1", text)
        if text:
            lines.append(text)
    return "\n".join(lines)[:NOTES_CHARS]


def running_version(settings: Settings) -> Version | None:
    """Return the version of this image (``APP_VERSION``); ``None`` for a non-release build."""
    return Version.parse(settings.app_version)


def update_service(
    settings: Settings,
    uow: UnitOfWork,
    clock: Clock,
    http: httpx.AsyncClient,
    *,
    lookup: bool = True,
    live: Callable[[], Settings] | None = None,
) -> UpdateService:
    """Return the update service the settings describe (web, worker and the CLI share it).

    Args:
        settings: Version, repository and token.
        uow: Opens database transactions.
        clock: Current time.
        http: Shared HTTP client.
        lookup: Whether this process looks up releases itself; the web process passes
            ``False`` and only reads the worker's checks (ADR 0029).
        live: Returns the current effective settings, for the channel, auto-apply, line and
            window, which the superuser changes in the portal (ADR 0067, 0116); the worker
            passes its live settings, the web process rebuilds the service on every change.
    """
    token = settings.update_check_token
    current = live or (lambda: settings)

    def channel() -> UpdateChannel:
        return UpdateChannel(current().update_channel)

    def window() -> UpdateWindow:
        now = current()
        try:
            return parse_window(now.update_window, ZoneInfo(now.admin_timezone))
        except (ValueError, ZoneInfoNotFoundError):  # pragma: no cover - the settings check both
            return ANY_TIME

    releases = (
        GitHubReleases(
            http, settings.update_repository, token.get_secret_value() if token else None
        )
        if settings.update_repository and lookup
        else None
    )
    return UpdateService(
        uow,
        clock,
        AdminService(uow, clock),
        current=running_version(settings),
        releases=releases,
        checking=settings.update_repository is not None,
        channel=channel,
        auto_apply=lambda: current().update_auto_apply,
        interval=lambda: timedelta(minutes=current().update_check_minutes),
        line=lambda: current().update_line,
        window=window,
        production=settings.environment == "prod",
    )

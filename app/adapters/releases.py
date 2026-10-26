"""``ReleaseSource`` on the GitHub releases API (ADR 0028).

Asks for the latest published release of one repository; GitHub leaves drafts and
pre-releases out of ``/releases/latest``. Only the tag and the release page are used: the tag
must be a version (``v1.4.0``) and the page must be this repository's release page, else the
release is ignored.
The optional token is for a private repository and needs read access to its contents only.
"""

from typing import Any

import httpx
import structlog

from app.adapters.webex_auth import http_failure
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.errors import DomainError
from app.domain.ports import Clock, IntegrationError, UnitOfWork
from app.domain.update_service import UpdateService
from app.domain.updates import Release, Version

log = structlog.get_logger()

API_BASE = "https://api.github.com"


class GitHubReleases:
    """Looks up the latest release of ``repository`` (``owner/name``).

    Args:
        http: Shared HTTP client.
        repository: ``owner/name`` on GitHub (validated by the settings).
        token: Optional read-only token for a private repository; never logged.
    """

    def __init__(self, http: httpx.AsyncClient, repository: str, token: str | None) -> None:
        """Store the collaborators."""
        self._http = http
        self._url = f"{API_BASE}/repos/{repository}/releases/latest"
        self._pages = f"https://github.com/{repository}/releases/"
        self._token = token

    async def latest(self) -> Release | None:
        """Return the latest release; ``None`` when there is none or it is not usable.

        Raises:
            IntegrationError: ``unauthorized``, ``rate_limited``, ``server_error``,
                ``rejected_<status>`` or ``unreachable``.
        """
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = await self._http.get(self._url, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("integration.unreachable", error=type(exc).__name__)
            raise IntegrationError("unreachable", retryable=True) from exc
        if response.status_code == 404:  # no release published yet
            return None
        if response.status_code != 200:
            raise http_failure(response)
        return self._release(response.json())

    def _release(self, data: Any) -> Release | None:
        """Return the release in a ``/releases/latest`` body, if it is a usable one."""
        tag = data.get("tag_name") if isinstance(data, dict) else None
        page = data.get("html_url") if isinstance(data, dict) else None
        version = Version.parse(tag) if isinstance(tag, str) else None
        if version is None or not isinstance(page, str):
            log.warning("updates.release_ignored", reason="not_a_version")
            return None
        if not page.startswith(self._pages):
            log.warning("updates.release_ignored", reason="foreign_page")
            return None
        try:
            return Release(version, page)
        except DomainError:
            log.warning("updates.release_ignored", reason="foreign_page")
            return None


def running_version(settings: Settings) -> Version | None:
    """Return the version of this image (``APP_VERSION``); ``None`` for a non-release build."""
    return Version.parse(settings.app_version)


def update_service(
    settings: Settings, uow: UnitOfWork, clock: Clock, http: httpx.AsyncClient
) -> UpdateService:
    """Return the update service the settings describe (web, worker and the CLI share it)."""
    token = settings.update_check_token
    releases = (
        GitHubReleases(
            http, settings.update_repository, token.get_secret_value() if token else None
        )
        if settings.update_repository
        else None
    )
    return UpdateService(
        uow, clock, AdminService(uow, clock), current=running_version(settings), releases=releases
    )

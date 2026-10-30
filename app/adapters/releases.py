"""``ReleaseSource`` on the GitHub releases API (ADR 0029, ADR 0059).

On the ``release`` channel it asks for the latest published release of one repository;
GitHub leaves drafts and pre-releases out of ``/releases/latest``. On the ``rc`` channel it
lists the newest releases and takes the highest version among releases and pre-releases
(drafts are never listed for a read token). Only the tag and the release page are used: the
tag must be a version (``v1.4.0``, ``v1.4.0-rc.2``) and the page must be this repository's
release page, else the release is ignored.
The optional token is for a private repository and needs read access to its contents only.
"""

from collections.abc import Callable
from typing import Any

import httpx
import structlog

from app.adapters.webex_auth import http_failure
from app.config import Settings
from app.domain.admin import AdminService
from app.domain.errors import DomainError
from app.domain.ports import Clock, IntegrationError, UnitOfWork
from app.domain.update_service import UpdateService
from app.domain.updates import Release, UpdateChannel, Version

log = structlog.get_logger()

API_BASE = "https://api.github.com"
LISTED = 30
"""How many of the newest releases the ``rc`` channel looks at; candidates come often."""


class GitHubReleases:
    """Looks up the latest release of ``repository`` (``owner/name``).

    Args:
        http: Shared HTTP client.
        repository: ``owner/name`` on GitHub (validated by the settings).
        token: Optional read-only token for a private repository; never logged.
        channel: ``release`` asks ``/releases/latest``; ``rc`` lists releases and pre-releases.
            A value, or a callable read on every lookup (the portal setting, ADR 0065).
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        repository: str,
        token: str | None,
        channel: UpdateChannel | Callable[[], UpdateChannel] = UpdateChannel.RELEASE,
    ) -> None:
        """Store the collaborators."""
        self._http = http
        self._channel = channel if callable(channel) else (lambda: channel)
        self._releases = f"{API_BASE}/repos/{repository}/releases"
        self._pages = f"https://github.com/{repository}/releases/"
        self._token = token

    def _url(self, channel: UpdateChannel) -> str:
        """Return the endpoint of ``channel``."""
        if channel is UpdateChannel.RELEASE:
            return f"{self._releases}/latest"
        return f"{self._releases}?per_page={LISTED}"

    async def latest(self) -> Release | None:
        """Return the newest release the channel offers; ``None`` when there is none usable.

        Raises:
            IntegrationError: ``unauthorized``, ``rate_limited``, ``server_error``,
                ``rejected_<status>`` or ``unreachable``.
        """
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        channel = self._channel()
        try:
            response = await self._http.get(self._url(channel), headers=headers)
        except httpx.HTTPError as exc:
            log.warning("integration.unreachable", error=type(exc).__name__)
            raise IntegrationError("unreachable", retryable=True) from exc
        if response.status_code == 404:  # no release published yet
            return None
        if response.status_code != 200:
            raise http_failure(response)
        data = response.json()
        if channel is UpdateChannel.RELEASE:
            return self._release(data)
        return self._newest(data)

    def _newest(self, data: Any) -> Release | None:
        """Return the highest usable version in a ``/releases`` list (``rc`` channel)."""
        if not isinstance(data, list):
            log.warning("updates.release_ignored", reason="not_a_list")
            return None
        usable = [
            release
            for item in data
            if isinstance(item, dict) and not item.get("draft")
            if (release := self._release(item)) is not None
        ]
        return max(usable, key=lambda release: release.version, default=None)

    def _release(self, data: Any) -> Release | None:
        """Return the release in one release body, if it is a usable one."""
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
        live: Returns the current effective settings, for the channel and auto-apply, which
            the superuser changes in the portal (ADR 0065); the worker passes its live
            settings, the web process rebuilds the service on every change instead.
    """
    token = settings.update_check_token
    current = live or (lambda: settings)

    def channel() -> UpdateChannel:
        return UpdateChannel(current().update_channel)

    releases = (
        GitHubReleases(
            http,
            settings.update_repository,
            token.get_secret_value() if token else None,
            channel,
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
    )

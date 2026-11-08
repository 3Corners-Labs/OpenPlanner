"""Reading and writing the configuration as a document tree (ADR 0099 points 3, 6 and 7).

A mixin of ``SqlTransaction``. ``read_configuration`` joins what the per-aggregate readers
(``config_*.py``) return into the tree of the document's shape, with the usage facts the rules
check against; ``apply_configuration`` hands each change of a plan to the writer of its kind, in
the order of the plan, and moves the settings version on once. The writers know one kind each;
this module knows only which writer belongs to which kind.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.crypto import SecretBox
from app.adapters.db import (
    config_alerts,
    config_channels,
    config_clients,
    config_group,
    config_login,
    config_organisation,
    config_platform,
    config_scenarios,
)
from app.adapters.db.config_common import Writer
from app.adapters.db.config_usage import read_usage
from app.domain.configuration_changes import Change, ConfigurationSnapshot
from app.domain.configuration_tree import Entity
from app.domain.errors import DomainError, ErrorCode

WRITERS: dict[str, Writer] = {
    **config_platform.WRITERS,
    **config_alerts.WRITERS,
    **config_organisation.WRITERS,
    **config_login.WRITERS,
    **config_channels.WRITERS,
    **config_scenarios.WRITERS,
    **config_group.WRITERS,
    **config_clients.WRITERS,
}
"""The writer of every kind of the version-1 format; a test checks the format is covered."""


class _Transaction(Protocol):
    """What these queries need from ``SqlTransaction``."""

    _session: AsyncSession
    _box: SecretBox | None

    async def settings_version(self) -> int: ...

    async def platform_settings(self) -> dict[str, object]: ...

    async def bump_settings_version(self) -> int: ...


def _present(children: dict[str, tuple[Entity, ...]]) -> dict[str, tuple[Entity, ...]]:
    """Return the collections that hold something: an absent one is untouched by a document."""
    return {name: members for name, members in children.items() if members}


class ConfigurationDocumentQueries:
    """Reads the configuration into a tree and applies the changes of a plan."""

    async def read_configuration(
        self: _Transaction,
        now: datetime,
        locked_settings: frozenset[str],
        *,
        secrets: bool = True,
    ) -> ConfigurationSnapshot:
        """Return the configuration tree, the usage facts and the settings version.

        Without ``secrets`` the readers get no settings key, so no sealed value is opened.
        """
        session = self._session
        box = self._box if secrets else None
        platform = await config_platform.read_platform(session, box)
        alerts = await config_alerts.read_alert_targets(session)
        organisations = await config_organisation.read_organisations(session, box)
        providers = await config_login.read_identity_providers(session, box)
        channels = await config_channels.read_channels(session, box)
        by_organisation, by_group = await config_scenarios.read_scenarios(session)
        groups = await config_group.read_groups(session)
        clients = await config_clients.read_api_clients(session, box)
        platform = replace(
            platform,
            children=_present({**platform.children, "alert_targets": alerts.get(None, ())}),
        )
        tree: list[Entity] = []
        for slug, organisation in organisations.items():
            own_groups = tuple(
                replace(
                    group,
                    children=_present(
                        {
                            **group.children,
                            "closure_scenarios": by_group.get((slug, group.key[0]), ()),
                        }
                    ),
                )
                for group in groups.get(slug, ())
            )
            children = {
                **organisation.children,
                **channels.get(slug, {}),
                "identity_providers": providers.get(slug, ()),
                "closure_scenarios": by_organisation.get(slug, ()),
                "alert_targets": alerts.get(slug, ()),
                "groups": own_groups,
            }
            tree.append(replace(organisation, children=_present(children)))
        root = Entity(
            "installation",
            children=_present(
                {"platform": (platform,), "organisations": tuple(tree), "api_clients": clients}
            ),
        )
        usage = await read_usage(session, now, locked_settings)
        return ConfigurationSnapshot(
            root,
            usage,
            await self.settings_version(),
            secrets_available=box is not None,
            settings=await self.platform_settings(),
        )

    async def apply_configuration(self: _Transaction, changes: Sequence[Change]) -> int:
        """Write the changes in order; return the new settings version.

        Raises:
            DomainError: ``invalid_configuration`` for a kind nothing can write.
        """
        for change in changes:
            writer = WRITERS.get(change.kind)
            if writer is None:
                raise DomainError(ErrorCode.INVALID_CONFIGURATION, change.kind)
            await writer(self._session, self._box, change)
        await config_login.ensure_signing_keys(self._session, self._box)
        return await self.bump_settings_version()

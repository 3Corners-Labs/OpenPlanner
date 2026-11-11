"""The version-1 format of the configuration document (ADR 0099 point 1 and 3).

The document is a tree of aggregates: ``platform`` (settings, secrets, the API switch, log
destinations, custom roles, holiday calendars, alert targets), ``organisations`` (each with its
settings, texts, branding, channels, identity providers, closure scenarios, alert targets,
``skills``, ``capacity_pools`` and ``groups``, each group with its ``legacy_pool`` and
``flows``) and ``api_clients``. Each part is declared in its own module; this one only joins
them into the registry that reading, planning, the JSON Schema and the table-coverage test all
use, and collects the rules across objects.
"""

from dataclasses import replace

from app.domain import (
    configuration_v1_alerts,
    configuration_v1_channels,
    configuration_v1_clients,
    configuration_v1_flow_rules,
    configuration_v1_flows,
    configuration_v1_group,
    configuration_v1_login,
    configuration_v1_organisation,
    configuration_v1_platform,
    configuration_v1_scenarios,
    configuration_v1_skills,
)
from app.domain.configuration_schema import Collection, Format, Kind, Shape

FORMAT_NAME = "openplanner.config/v1"
"""The value of a document's ``format`` property."""

_PLATFORM = replace(
    configuration_v1_platform.PLATFORM,
    collections=(
        *configuration_v1_platform.PLATFORM.collections,
        configuration_v1_alerts.PLATFORM_ALERT_TARGETS,
    ),
)
_GROUP = replace(
    configuration_v1_group.GROUP,
    collections=(
        *configuration_v1_group.GROUP.collections,
        configuration_v1_scenarios.GROUP_SCENARIOS,
        configuration_v1_flows.GROUP_LEGACY_POOL,
        configuration_v1_flows.GROUP_FLOWS,
    ),
)
_ORGANISATION = replace(
    configuration_v1_organisation.ORGANISATION,
    collections=(
        *configuration_v1_organisation.ORGANISATION.collections,
        configuration_v1_channels.EMAIL_CHANNEL,
        configuration_v1_channels.SMS_CHANNEL,
        configuration_v1_login.IDENTITY_PROVIDERS,
        configuration_v1_scenarios.ORGANISATION_SCENARIOS,
        configuration_v1_alerts.ORGANISATION_ALERT_TARGETS,
        configuration_v1_skills.SKILLS,
        configuration_v1_skills.CAPACITY_POOLS,
        Collection("groups", _GROUP, Shape.ENTITIES, "The organisation's bookable teams."),
    ),
)
_ROOT = Kind(
    "installation",
    collections=(
        Collection("platform", _PLATFORM, Shape.SINGLE, "What the whole installation shares."),
        Collection("organisations", _ORGANISATION, Shape.ENTITIES, "The client organisations."),
        Collection(
            "api_clients", configuration_v1_clients.API_CLIENT, Shape.ENTITIES, "Bot clients."
        ),
    ),
    description="One installation's configuration, or one organisation's.",
)

V1 = Format(
    FORMAT_NAME,
    _ROOT,
    integrity=(
        *configuration_v1_platform.INTEGRITY,
        *configuration_v1_organisation.INTEGRITY,
        *configuration_v1_login.INTEGRITY,
        *configuration_v1_channels.INTEGRITY,
        *configuration_v1_scenarios.INTEGRITY,
        *configuration_v1_alerts.INTEGRITY,
        *configuration_v1_group.INTEGRITY,
        *configuration_v1_flow_rules.INTEGRITY,
        *configuration_v1_clients.INTEGRITY,
    ),
)

FORMATS: dict[str, Format] = {V1.name: V1}
"""Every format version this release reads; an export always writes the newest (point 1)."""

"""Which dispatcher a group's task goes to (ADR 0032 points 9 and 11).

An organisation with a connection of its own dispatches through it, and only through it: a task
of one tenant never goes to another. An organisation without one uses the installation's
dispatcher for its platform, the fallback of point 11 for one minor.

    organisation connected? ─yes─► its own dispatcher, or ``connection_incomplete`` (retried,
                            │       then the fallback of ADR 0082 takes over)
                            ─no──► the installation's dispatcher for the kind (or none)
"""

from app.domain.contact_center import ContactCenterKind
from app.domain.ports import (
    ContactCenterDispatcher,
    ContactCenterDispatchers,
    IntegrationError,
    OrganisationDispatchers,
)

INCOMPLETE = "connection_incomplete"
"""The organisation's own connection cannot dispatch yet; retryable, so the task waits."""


class DispatcherChoice:
    """Chooses the dispatcher of an organisation's task.

    Args:
        installation: The installation's dispatcher per platform; empty when none is set.
        organisations: The organisations' own dispatchers; ``None`` when none can be kept.
    """

    def __init__(
        self,
        installation: ContactCenterDispatchers,
        organisations: OrganisationDispatchers | None = None,
    ) -> None:
        """Store both sources."""
        self._installation = installation
        self._organisations = organisations

    @property
    def configured(self) -> bool:
        """Whether any task can be dispatched at all, by the installation or an organisation."""
        return bool(self._installation) or self._organisations is not None

    async def for_organisation(
        self, organisation_id: int, kind: ContactCenterKind
    ) -> ContactCenterDispatcher | None:
        """Return the dispatcher for a task of ``organisation_id`` on platform ``kind``.

        ``None`` only when the organisation has no connection and the installation none for
        ``kind`` either: like a group without a contact center.

        Raises:
            IntegrationError: ``connection_incomplete`` (retryable) when the organisation's own
                connection cannot dispatch yet; its task never goes to another tenant.
        """
        if self._organisations is not None:
            own = await self._organisations.own(organisation_id, kind)
            if own is not None:
                if own.dispatcher is None:
                    raise IntegrationError(INCOMPLETE, retryable=True)
                return own.dispatcher
        return self._installation.get(kind)

"""Which dispatcher a group's task goes to (ADR 0032 points 9 and 11).

An organisation with a connection of its own dispatches through it, and only through it: a task
of one tenant never goes to another. An organisation without one uses the installation's
dispatcher for its platform, the fallback of point 11 for one minor.

    organisation connected? ─yes─► its own dispatcher (or none: the task waits)
                            ─no──► the installation's dispatcher for the kind
"""

from app.domain.contact_center import ContactCenterKind
from app.domain.ports import (
    ContactCenterDispatcher,
    ContactCenterDispatchers,
    OrganisationDispatchers,
)


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
        self, organisation_id: int | None, kind: ContactCenterKind
    ) -> ContactCenterDispatcher | None:
        """Return the dispatcher for a task of ``organisation_id`` on platform ``kind``."""
        if (
            self._organisations is not None
            and organisation_id is not None
            and await self._organisations.connected(organisation_id)
        ):
            return await self._organisations.dispatcher(organisation_id, kind)
        return self._installation.get(kind)

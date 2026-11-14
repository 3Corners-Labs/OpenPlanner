"""The staff services of the web process, built once from the settings.

``StaffLifecycleService`` (suspend, reinstate, erase; ADR 0046) and ``StaffScreenNames`` (the
screen name of a staff member and who is named to the participant of a call; ADR 0123 step 5c) both
work on the staff directory. They are built here so that the application's entry point wires one
call for the directory's services (ADR 0005).
"""

from app.config import Settings
from app.domain.ports import Clock, UnitOfWork
from app.domain.staff_lifecycle import StaffLifecycleService
from app.domain.staff_screen_names import StaffScreenNames
from app.keys import agent_pseudonym_key, agent_pseudonym_key_previous


def staff_parts(
    uow: UnitOfWork, clock: Clock, settings: Settings
) -> tuple[StaffLifecycleService, StaffScreenNames]:
    """Return the lifecycle service and the screen-name service of the staff directory.

    The screen-name service is given the keys of the agents' pseudonyms, as the usage facts are:
    a booking's claim names who accepted it only by a pseudonym.
    """
    keys = [
        key
        for key in (agent_pseudonym_key(settings), agent_pseudonym_key_previous(settings))
        if key is not None
    ]
    return StaffLifecycleService(uow, clock), StaffScreenNames(uow, clock, keys)

"""What a route depends on: the one walk over a FastAPI route's dependency tree.

For code that decides from the guards a route declares rather than from its path: which
permission a form's action needs while maintenance is on (``app.admin.frozen_forms``), and
whether a ``GET`` is an action that a login must not replay (``app.admin.return_to``).
"""

from fastapi.dependencies.models import Dependant


def dependency_calls(route: object) -> list[object]:
    """Return every callable ``route`` depends on, nested dependencies included.

    Args:
        route: A route of the application, such as the matched ``request.scope["route"]``.
            Anything without a FastAPI dependency tree (``None`` for an unmatched request)
            depends on nothing.

    Returns:
        The dependency callables, each ``Depends(...)`` target once per place it is declared.
    """
    dependant = getattr(route, "dependant", None)
    if not isinstance(dependant, Dependant):
        return []
    found: list[object] = []
    stack = list(dependant.dependencies)
    while stack:
        dependency = stack.pop()
        found.append(dependency.call)
        stack.extend(dependency.dependencies)
    return found

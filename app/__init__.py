"""OpenPlanner: group-booking service with Webex Contact Center dispatch.

Layers, from outside in (enforced by import-linter, see ``pyproject.toml``):

- ``app.main``: composition root; builds the FastAPI app and wires adapters to ports.
- ``app.api``, ``app.admin``, ``app.jobs``: entry points (public HTTP API, admin UI, worker).
  They never import each other.
- ``app.adapters``: implementations of the ports in ``app.domain.ports`` (database, Webex,
  mail, SMS, IdP, clock).
- ``app.domain``: pure Python business rules. Imports no framework and no adapter.

Cross-cutting modules (``app.config``, ``app.logging``) may be imported by every layer
except the domain.
"""

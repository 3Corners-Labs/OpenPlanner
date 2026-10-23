"""ORM models. Importing this module registers every table on ``Base.metadata``.

Tables are added from work package 2 onwards.
"""

from app.adapters.db.base import Base

__all__ = ["Base"]

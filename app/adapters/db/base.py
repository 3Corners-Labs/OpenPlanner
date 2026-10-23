"""Declarative ORM base shared by all models."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Deterministic constraint names keep Alembic migrations reversible on SQLite and Postgres."""


class Base(DeclarativeBase):
    """Base class for every ORM model; carries the shared ``MetaData``."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

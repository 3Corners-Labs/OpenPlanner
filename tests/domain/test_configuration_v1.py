"""The version-1 registry as a whole (ADR 0099 point 3): every table and column is reached."""

from app.domain.authorization import LEVELS, Level
from app.domain.configuration_package import CONFIGURATION_TABLES
from app.domain.configuration_schema import Anchor, Collection, Kind
from app.domain.configuration_v1 import FORMATS, V1
from tests.support.configuration_columns import uncovered_columns


def test_the_format_is_registered_under_its_name() -> None:
    assert FORMATS == {"openplanner.config/v1": V1}
    assert [c.name for c in V1.root.collections] == ["platform", "organisations", "api_clients"]


def test_every_configuration_table_is_reached_by_a_kind_and_no_other_table_is() -> None:
    assert V1.tables() == CONFIGURATION_TABLES


def test_every_column_of_those_tables_is_in_the_document_derived_or_a_key() -> None:
    assert uncovered_columns(V1.kinds()) == {}


def test_kind_names_and_collection_names_do_not_clash() -> None:
    names = [kind.name for kind in V1.kinds()]
    assert len(names) == len(set(names))
    for kind in V1.kinds():
        collections = [c.name for c in kind.collections]
        fields = {f.name for f in kind.fields}
        assert len(collections) == len(set(collections)), kind.name
        assert not fields & set(collections), kind.name


def _needs(kind: Kind) -> list[tuple[str, Level]]:
    """Every permission a kind can ask, with the level it is checked at."""
    perms = [kind.permission, kind.create_permission, kind.delete_permission]
    perms += [spec.permission for spec in kind.fields]
    return [(p.value, LEVELS[p]) for p in perms if p is not None]


def _walk(collection: Collection, organisation: bool, group: bool, inherited: bool) -> None:
    """Check that each permission's level can be met where the kind sits in the tree."""
    kind = collection.kind
    organisation = (
        organisation or kind.anchor is Anchor.ORGANISATION or bool(kind.organisation_field)
    )
    group = group or kind.anchor is Anchor.GROUP
    inherited = inherited or kind.permission is not None
    for name, level in _needs(kind):
        assert level is not Level.GROUP or group, f"{kind.name}: {name} needs a group"
        assert level is not Level.ORGANISATION or organisation, (
            f"{kind.name}: {name} needs an organisation"
        )
    assert inherited, f"{kind.name} has no permission of its own or from its parent"
    for child in kind.collections:
        _walk(child, organisation, group, inherited)


def test_every_permission_can_be_checked_where_its_kind_sits() -> None:
    for collection in V1.root.collections:
        _walk(collection, False, False, False)

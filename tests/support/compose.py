"""Read this repository's Compose files as written, overlays and their merge tags included."""

from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).parents[2]


class _ComposeLoader(yaml.SafeLoader):
    """A safe loader that reads Compose's merge tags (``!override``, ``!reset``) as plain values."""


def _untagged(loader: yaml.SafeLoader, _tag: str, node: yaml.Node) -> Any:
    """Construct a tagged node as the mapping, sequence or scalar it holds."""
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_ComposeLoader.add_multi_constructor("!", _untagged)


def compose_file(path: str = "docker-compose.yml") -> dict[str, Any]:
    """Return a Compose file of the repository, by its path from the repository root.

    Args:
        path: The file, such as ``docker-compose.yml`` or ``deploy/proxy/compose.pebble.yml``.

    Returns:
        The file as written: unmerged, variables unresolved, merge tags read as plain values.
    """
    text = (REPO / path).read_text()
    loaded: dict[str, Any] = yaml.load(text, Loader=_ComposeLoader)  # noqa: S506 - a SafeLoader
    return loaded

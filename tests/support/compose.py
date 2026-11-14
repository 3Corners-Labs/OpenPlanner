"""Read this repository's Compose files as written, overlays and their merge tags included."""

import ipaddress
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


def fixed_address(service: str, network: str) -> ipaddress.IPv4Address:
    """Return the fixed IPv4 address ``docker-compose.yml`` gives a service on a network.

    Args:
        service: The compose service, such as ``idp-db``.
        network: The network it has the address on, such as ``idp-store``.

    Returns:
        The address (ADR 0125 step 2).
    """
    networks = compose_file()["services"][service]["networks"]
    return ipaddress.IPv4Address(networks[network]["ipv4_address"])

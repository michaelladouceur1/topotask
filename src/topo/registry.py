"""Decorator-based node registry + entry-point plugin loading."""
from __future__ import annotations

from importlib.metadata import entry_points

_REGISTRY: dict[str, type] = {}


def register(type_name: str):
    def deco(cls):
        if type_name in _REGISTRY:
            raise ValueError(f"Node type {type_name!r} already registered")
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        return cls
    return deco


def get_node_class(type_name: str) -> type:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(f"Unknown node type {type_name!r}. Known: {sorted(_REGISTRY)}") from None


def load_plugins() -> None:
    """Import built-in nodes and any third-party `topo.nodes` entry points."""
    from . import nodes  # noqa: F401  (registers built-ins on import)
    for ep in entry_points(group="topo.nodes"):
        ep.load()

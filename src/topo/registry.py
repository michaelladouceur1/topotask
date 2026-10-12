"""Decorator-based node registry + plugin loading."""
from __future__ import annotations

import importlib
import os
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
    """Import modules that register node types (`topo` ships with none built in).

    Two optional mechanisms:

    - ``TOPO_PLUGINS``: a comma-separated list of importable module names. Handy
      for local projects and examples that are not installed as a package.
    - the ``topo.nodes`` entry-point group, the distributable path::

          [project.entry-points."topo.nodes"]
          my_nodes = "my_pkg.nodes"
    """
    for module in os.environ.get("TOPO_PLUGINS", "").split(","):
        module = module.strip()
        if module:
            importlib.import_module(module)
    for ep in entry_points(group="topo.nodes"):
        ep.load()

from __future__ import annotations

from topotask.node import Node, NodeParams
from topotask.registry import register


@register("scene.load")
class SceneLoad(Node):
    class Params(NodeParams):
        path: str = "scenes/table.yaml"
    outputs = ("scene",)

    def run(self):
        # Real version: parse a world config / USD / URDF into your scene representation.
        return {"scene": {"path": self.params.path, "table_z": 0.1, "padding": 0.0}}


@register("scene.inflate_obstacles")
class InflateObstacles(Node):
    """Example 'fallback' branch node: runs only when simulation reports a collision."""
    class Params(NodeParams):
        extra_padding: float = 0.05
    outputs = ("scene",)

    def run(self, scene):
        return {"scene": {**scene, "padding": scene["padding"] + self.params.extra_padding}}

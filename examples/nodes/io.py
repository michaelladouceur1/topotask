from __future__ import annotations

from typing import Literal

from topo.node import Node, NodeParams
from topo.registry import register


@register("io.export_trajectory")
class ExportTrajectory(Node):
    class Params(NodeParams):
        format: Literal["json", "csv"] = "json"
    outputs = ("payload",)

    def run(self, trajectory):
        return {"payload": {"format": self.params.format, "waypoints": trajectory}}

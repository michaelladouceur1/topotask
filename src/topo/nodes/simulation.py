from __future__ import annotations

from ..node import Node, NodeParams
from ..registry import register


@register("sim.warp")
class WarpSim(Node):
    class Params(NodeParams):
        min_clearance: float = 0.02
    outputs = ("result", "collision_free")

    def setup(self):
        # Real version: wp.init(); build the model/kernels once; optionally capture a
        # CUDA graph with wp.ScopedCapture() so per-request launch overhead is tiny.
        pass

    def run(self, trajectory, scene):
        # Real version: step the sim over the trajectory using Warp kernels; share
        # tensors with upstream nodes via wp.from_torch / wp.to_torch (zero-copy).
        clearance = min(wp[2] for wp in trajectory) - scene["table_z"] - scene["padding"]
        return {"result": {"min_clearance": round(clearance, 4), "steps": len(trajectory)},
                "collision_free": clearance >= self.params.min_clearance}

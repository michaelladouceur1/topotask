from __future__ import annotations

from ..node import Node, NodeParams
from ..registry import register


@register("planner.curobo")
class CuRoboPlanner(Node):
    class Params(NodeParams):
        robot: str = "franka.yml"
        max_attempts: int = 4
    outputs = ("trajectory",)

    def setup(self):
        # Real version (expensive, so done ONCE here, not per request):
        #   from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig
        #   cfg = MotionGenConfig.load_from_robot_config(self.params.robot, world_cfg, ...)
        #   self.motion_gen = MotionGen(cfg); self.motion_gen.warmup()
        self.start = [0.0, 0.0, 0.5]

    def run(self, scene, goal):
        # Real version: self.motion_gen.plan_single(start_state, Pose(...), plan_cfg),
        # keeping the result as torch tensors on the GPU.
        n = 5
        traj = [[s + (g - s) * i / (n - 1) for s, g in zip(self.start, goal)] for i in range(n)]
        return {"trajectory": traj}

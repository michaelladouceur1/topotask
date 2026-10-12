import time

import pytest

from topo import Node, NodeParams, Pipeline, PipelineError, PipelineSpec, load_pipeline_spec, register

CFG = "configs/pipelines/plan_and_simulate.yaml"


def make(path=CFG, overrides=None):
    p = Pipeline(load_pipeline_spec(path, overrides))
    p.setup()
    return p


def test_collision_free_takes_export_branch():
    out = make().run({"goal": [0.3, 0.2, 0.4]})
    assert out["exported"]["waypoints"] and out["suggested_scene"] is None


def test_collision_takes_fallback_branch():
    out = make().run({"goal": [0.3, 0.2, 0.05]})
    assert out["exported"] is None and out["suggested_scene"]["padding"] == 0.05


def test_override_changes_param():
    out = make(overrides=["nodes.simulate.params.min_clearance=0.5"]).run({"goal": [0.3, 0.2, 0.4]})
    assert out["exported"] is None   # clearance 0.2 < 0.5 now


def test_bad_param_rejected_at_load():
    with pytest.raises(Exception, match="max_attempt"):
        make(overrides=["nodes.plan.params.max_attempt=3"])  # typo -> extra="forbid"


def test_bad_port_rejected_at_load():
    spec = load_pipeline_spec(CFG)
    spec.nodes["export"].inputs["trajectory"] = "plan.nope"
    with pytest.raises(PipelineError, match="no output 'nope'"):
        Pipeline(spec)


def test_cycle_rejected():
    spec = PipelineSpec.model_validate({"name": "c", "nodes": {
        "a": {"type": "t.pass", "inputs": {"x": "b.y"}},
        "b": {"type": "t.pass", "inputs": {"x": "a.y"}}}})
    with pytest.raises(PipelineError, match="cycle"):
        Pipeline(spec)


@register("t.pass")
class Pass(Node):
    outputs = ("y",)
    def run(self, x=None):
        return {"y": x}


@register("t.sleep")
class Sleep(Node):
    class Params(NodeParams):
        secs: float = 0.3
    outputs = ("y",)
    def run(self):
        time.sleep(self.params.secs)
        return {"y": 1}


def test_independent_nodes_run_in_parallel():
    spec = PipelineSpec.model_validate({"name": "par", "nodes": {
        "a": {"type": "t.sleep"}, "b": {"type": "t.sleep"}},
        "outputs": {"a": "a.y", "b": "b.y"}})
    p = Pipeline(spec); p.setup()
    t = time.perf_counter(); p.run({}); dt = time.perf_counter() - t
    assert dt < 0.5   # serial would be ~0.6s

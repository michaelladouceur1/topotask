"""Generic executor tests. Node types are defined here, so the core needs no built-ins."""
import time

import pytest

from topotask import Node, NodeParams, Pipeline, PipelineError, PipelineSpec, register


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


def test_cycle_rejected():
    spec = PipelineSpec.model_validate({"name": "c", "nodes": {
        "a": {"type": "t.pass", "inputs": {"x": "b.y"}},
        "b": {"type": "t.pass", "inputs": {"x": "a.y"}}}})
    with pytest.raises(PipelineError, match="cycle"):
        Pipeline(spec)


def test_independent_nodes_run_in_parallel():
    spec = PipelineSpec.model_validate({"name": "par", "nodes": {
        "a": {"type": "t.sleep"}, "b": {"type": "t.sleep"}},
        "outputs": {"a": "a.y", "b": "b.y"}})
    p = Pipeline(spec); p.setup()
    t = time.perf_counter(); p.run({}); dt = time.perf_counter() - t
    assert dt < 0.5   # serial would be ~0.6s

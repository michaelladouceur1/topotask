"""Analytics tests: DOT/Mermaid emission (dependency-free) + optional rendering.

Node types are defined here so the core keeps no built-ins, mirroring
``tests/test_executor.py``.
"""
import json
import shutil

import pytest

from topotask import Node, NodeParams, NodeStatus, Pipeline, PipelineSpec, register
from topotask.viz import render, render_all, to_dot, to_mermaid, write_report_json


@register("v.src")
class Src(Node):
    outputs = ("y",)

    def run(self):
        return {"y": 1}


@register("v.flag")
class Flag(Node):
    class Params(NodeParams):
        value: bool = True
    outputs = ("ok",)

    def run(self):
        return {"ok": self.params.value}


@register("v.sink")
class Sink(Node):
    outputs = ("z",)

    def run(self, x=None):
        return {"z": x}


@register("v.fail")
class Fail(Node):
    outputs = ("y",)

    def run(self):
        raise RuntimeError("boom")


SPEC = PipelineSpec.model_validate({
    "name": "demo",
    "nodes": {
        "a": {"type": "v.src"},
        "b": {"type": "v.sink", "inputs": {"x": "a.y"}},
        "c": {"type": "v.sink", "inputs": {"x": "a.y"}, "when": "a.y"},
    },
    "outputs": {"out": "b.z"},
})

FAIL_SPEC = PipelineSpec.model_validate({
    "name": "failing",
    "nodes": {
        "a": {"type": "v.fail"},
        "b": {"type": "v.sink", "inputs": {"x": "a.y"}},
    },
    "outputs": {"out": "b.z"},
})

SKIP_SPEC = PipelineSpec.model_validate({
    "name": "skipping",
    "nodes": {
        "flag": {"type": "v.flag", "params": {"value": False}},
        "sink": {"type": "v.sink", "inputs": {"x": "flag.ok"}, "when": "flag.ok"},
    },
    "outputs": {"out": "sink.z"},
})


def _run(spec):
    p = Pipeline(spec)
    p.setup()
    try:
        return p.run_report({})
    finally:
        p.close()


# ---- report statuses -------------------------------------------------------

def test_report_success():
    r = _run(SPEC)
    assert r.ok
    assert r.statuses["a"] == NodeStatus.SUCCEEDED
    assert r.statuses["b"] == NodeStatus.SUCCEEDED
    assert r.outputs["out"] == 1
    assert "a" in r.durations


def test_report_failure_marks_downstream_not_run():
    r = _run(FAIL_SPEC)
    assert not r.ok
    assert r.statuses["a"] == NodeStatus.FAILED
    assert r.statuses["b"] == NodeStatus.NOT_RUN
    assert r.outputs["out"] is None
    assert "boom" in r.errors["a"]


def test_report_when_gate_skips_node():
    r = _run(SKIP_SPEC)
    assert r.ok
    assert r.statuses["flag"] == NodeStatus.SUCCEEDED
    assert r.statuses["sink"] == NodeStatus.SKIPPED
    assert r.outputs["out"] is None


def test_run_still_raises_and_keeps_report():
    p = Pipeline(FAIL_SPEC)
    p.setup()
    try:
        with pytest.raises(Exception, match="boom"):
            p.run({})
    finally:
        p.close()
    assert p.last_report is not None and not p.last_report.ok


# ---- DOT / Mermaid emission ------------------------------------------------

def test_to_dot_structure():
    dot = to_dot(SPEC)
    assert dot.startswith('digraph "demo"')
    assert '"a" [label="a\\nv.src"]' in dot
    assert '"a" -> "b" [label="x"]' in dot            # data edge
    assert 'style=dashed' in dot                       # when gate edge
    assert 'label="when y"' in dot
    assert 'shape=oval' in dot                         # declared output sink
    assert dot.rstrip().endswith("}")


def test_to_dot_results_colors_and_legend():
    dot = to_dot(SPEC, report=_run(SPEC))
    assert 'fillcolor="#c8e6c9"' in dot                # succeeded = green
    assert "cluster_legend" in dot
    assert "legend_failed" in dot


def test_to_dot_marks_skipped_dashed():
    dot = to_dot(SKIP_SPEC, report=_run(SKIP_SPEC))
    assert 'fillcolor="#e0e0e0"' in dot
    assert 'style="rounded,filled,dashed"' in dot


def test_to_mermaid_structure_and_classes():
    mmd = to_mermaid(SPEC, report=_run(SPEC))
    assert mmd.startswith("flowchart LR")
    assert "-->|x|" in mmd
    assert "-.->|when y|" in mmd
    assert "classDef succeeded" in mmd


# ---- JSON + rendering ------------------------------------------------------

def test_write_report_json(tmp_path):
    path = write_report_json(_run(FAIL_SPEC), tmp_path / "report.json")
    data = json.loads(path.read_text())
    assert data["ok"] is False
    assert data["statuses"]["a"] == "failed"
    assert data["statuses"]["b"] == "not_run"


@pytest.mark.skipif(shutil.which("dot") is None, reason="Graphviz 'dot' not on PATH")
def test_render_images(tmp_path):
    pytest.importorskip("graphviz")
    report = _run(SPEC)
    written = render_all(SPEC, tmp_path, report=report)
    names = {p.name for p in written}
    assert names == {"graph.png", "results.png", "report.json"}
    assert all(p.exists() and p.stat().st_size > 0 for p in written)


@pytest.mark.skipif(shutil.which("dot") is None, reason="Graphviz 'dot' not on PATH")
def test_render_svg(tmp_path):
    pytest.importorskip("graphviz")
    out = render(SPEC, tmp_path / "graph.svg", fmt="svg")
    assert out.exists() and out.suffix == ".svg"

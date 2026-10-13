"""Tests for multi-condition `when` gates: lists (AND), any/all combinators,
comparisons, and skipped-branch handling. Node types live here so the core
keeps no built-ins (mirroring tests/test_executor.py)."""
import pytest

from topotask import (Node, NodeParams, NodeStatus, Pipeline, PipelineError,
                      PipelineSpec, register)


@register("c.num")
class Num(Node):
    class Params(NodeParams):
        value: float = 0
    outputs = ("x",)

    def run(self):
        return {"x": self.params.value}


@register("c.dict")
class DictNode(Node):
    outputs = ("result",)

    def run(self):
        return {"result": {"steps": 7, "clearance": 0.1}}


@register("c.text")
class Text(Node):
    outputs = ("s",)

    def run(self):
        return {"s": "json"}


@register("c.sink")
class Sink(Node):
    outputs = ("z",)

    def run(self, x=None):
        return {"z": x}


def _spec(nodes, outputs=None):
    return PipelineSpec.model_validate(
        {"name": "cond", "nodes": nodes, "outputs": outputs or {}})


def _run(spec, inputs=None):
    p = Pipeline(spec)
    p.setup()
    try:
        return p.run_report(inputs or {})
    finally:
        p.close()


NUM5 = {"type": "c.num", "params": {"value": 5}}
NUM0 = {"type": "c.num", "params": {"value": 0}}


# ---- list form is AND -------------------------------------------------------

def test_list_of_conditions_requires_all():
    r = _run(_spec({
        "a": NUM5, "b": NUM0,
        "ok": {"type": "c.sink", "inputs": {"x": "a.x"},
               "when": ["a.x > 3", "b.x >= 0"]},
        "no": {"type": "c.sink", "inputs": {"x": "a.x"},
               "when": ["a.x > 3", "b.x > 0"]},
    }))
    assert r.statuses["ok"] == NodeStatus.SUCCEEDED
    assert r.statuses["no"] == NodeStatus.SKIPPED


def test_conditions_may_span_multiple_nodes():
    r = _run(_spec({
        "a": NUM5, "b": NUM0,
        "gate": {"type": "c.sink", "inputs": {"x": "a.x"},
                 "when": ["a.x == 5", "b.x == 0"]},
    }))
    assert r.statuses["gate"] == NodeStatus.SUCCEEDED


# ---- explicit any/all -------------------------------------------------------

def test_any_combinator():
    r = _run(_spec({
        "a": NUM5, "b": NUM0,
        "yes": {"type": "c.sink", "when": {"any": ["a.x > 3", "b.x > 0"]}},
        "no": {"type": "c.sink", "when": {"any": ["a.x > 30", "b.x > 0"]}},
    }))
    assert r.statuses["yes"] == NodeStatus.SUCCEEDED
    assert r.statuses["no"] == NodeStatus.SKIPPED


def test_all_combinator_nests():
    r = _run(_spec({
        "a": NUM5, "b": NUM0,
        "skip": {"type": "c.sink",
                 "when": {"all": ["a.x > 0", {"any": ["b.x > 0", "b.x < 0"]}]}},
        "run": {"type": "c.sink",
                "when": {"all": ["a.x > 0", {"any": ["b.x >= 0", "b.x < 0"]}]}},
    }))
    assert r.statuses["skip"] == NodeStatus.SKIPPED
    assert r.statuses["run"] == NodeStatus.SUCCEEDED


# ---- comparisons ------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ("a.x == 5", True),
    ("a.x != 5", False),
    ("a.x <= 5", True),
    ("a.x >= 5", True),
    ("a.x < 6", True),
    ("a.x > 5", False),
    ("a.x > 3", True),
])
def test_comparison_operators(expr, expected):
    r = _run(_spec({"a": NUM5, "g": {"type": "c.sink", "when": expr}}))
    want = NodeStatus.SUCCEEDED if expected else NodeStatus.SKIPPED
    assert r.statuses["g"] == want


def test_negated_comparison():
    r = _run(_spec({
        "a": NUM5,
        "skip": {"type": "c.sink", "when": "!a.x > 3"},
        "run": {"type": "c.sink", "when": "!a.x > 9"},
    }))
    assert r.statuses["skip"] == NodeStatus.SKIPPED
    assert r.statuses["run"] == NodeStatus.SUCCEEDED


def test_string_literal_comparison():
    r = _run(_spec({
        "t": {"type": "c.text"},
        "g": {"type": "c.sink", "when": 't.s == "json"'},
    }))
    assert r.statuses["g"] == NodeStatus.SUCCEEDED


def test_nested_output_path_in_condition():
    r = _run(_spec({
        "d": {"type": "c.dict"},
        "g": {"type": "c.sink", "when": "d.result.steps > 5"},
        "h": {"type": "c.sink", "when": "d.result.clearance < 0.05"},
    }))
    assert r.statuses["g"] == NodeStatus.SUCCEEDED
    assert r.statuses["h"] == NodeStatus.SKIPPED


def test_incomparable_types_raise():
    spec = _spec({"a": NUM5, "g": {"type": "c.sink", "when": 'a.x > "hi"'}})
    p = Pipeline(spec)
    p.setup()
    try:
        with pytest.raises(PipelineError, match="Cannot compare"):
            p.run_report({})
    finally:
        p.close()


# ---- skipped gate sources ---------------------------------------------------

def test_all_with_skipped_source_skips():
    r = _run(_spec({
        "a": NUM5,
        "b": {"type": "c.num", "params": {"value": 1}, "when": "a.x > 100"},  # skipped
        "g": {"type": "c.sink", "when": ["b.x >= 0", "a.x > 0"]},
    }))
    assert r.statuses["b"] == NodeStatus.SKIPPED
    assert r.statuses["g"] == NodeStatus.SKIPPED


def test_any_survives_a_skipped_branch():
    r = _run(_spec({
        "a": NUM5,
        "b": {"type": "c.num", "params": {"value": 1}, "when": "a.x > 100"},  # skipped
        "g": {"type": "c.sink", "when": {"any": ["b.x > 0", "a.x > 0"]}},
    }))
    assert r.statuses["b"] == NodeStatus.SKIPPED
    assert r.statuses["g"] == NodeStatus.SUCCEEDED


# ---- load-time validation ---------------------------------------------------

def test_unknown_node_in_condition_rejected():
    with pytest.raises(ValueError, match="unknown node"):
        _spec({"a": NUM5, "g": {"type": "c.sink", "when": "ghost.x"}})


def test_unknown_port_in_condition_rejected():
    spec = _spec({"a": NUM5, "g": {"type": "c.sink", "when": "a.nope"}})
    with pytest.raises(PipelineError, match="no output 'nope'"):
        Pipeline(spec)


def test_bad_condition_literal_rejected():
    with pytest.raises(ValueError, match="Bad literal"):
        _spec({"a": NUM5, "g": {"type": "c.sink", "when": "a.x > wat"}})


def test_empty_condition_list_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        _spec({"g": {"type": "c.sink", "when": []}})


def test_unknown_combinator_rejected():
    with pytest.raises(ValueError, match="unknown combinator"):
        _spec({"a": NUM5, "g": {"type": "c.sink", "when": {"none": ["a.x"]}}})


def test_bad_reference_in_condition_rejected():
    with pytest.raises(ValueError, match="Bad reference"):
        _spec({"g": {"type": "c.sink", "when": "justanode"}})

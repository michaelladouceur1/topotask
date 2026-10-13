"""Exercises the example nodes + pipeline under examples/configs/pipelines."""

from pathlib import Path

import pytest

import examples.nodes  # noqa: F401  (importing registers the example node types)
from topotask import Pipeline, PipelineError, load_pipeline_spec

CFG = Path(__file__).resolve().parents[1] / "configs" / "pipelines" / "plan_and_simulate.yaml"


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


def test_multi_condition_gate_skips_on_short_plan():
    # All conditions must hold: collision-free (yes) but steps 2 > 3 is false.
    out = make(overrides=["nodes.plan.params.num_steps=2"]).run({"goal": [0.3, 0.2, 0.4]})
    assert out["exported"] is None and out["suggested_scene"] is None


def test_bad_param_rejected_at_load():
    with pytest.raises(Exception, match="max_attempt"):
        make(overrides=["nodes.plan.params.max_attempt=3"])  # typo -> extra="forbid"


def test_bad_port_rejected_at_load():
    spec = load_pipeline_spec(CFG)
    spec.nodes["export"].inputs["trajectory"] = "plan.nope"
    with pytest.raises(PipelineError, match="no output 'nope'"):
        Pipeline(spec)

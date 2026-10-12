# topo

Composable DAG pipelines for robotics and simulation: **Pydantic-validated YAML
configs, a small threaded executor, and an optional FastAPI server**.

`topo` is a *framework*, not a library of steps. It decides how nodes are
configured, ordered, validated, and run; you provide the nodes. That keeps it
usable in any project — and safe to publish, since nothing implementation
specific is baked in.

- **Pydantic-validated configs.** Pipeline YAML is loaded through OmegaConf
  (interpolation, `${oc.env:...}`, CLI dot-list overrides) and validated with
  Pydantic models before anything runs.
- **A declarative node model.** Each node declares its Pydantic `Params` and its
  `outputs`, then implements `setup()` (once: load models, warm up kernels),
  `run()` (per request), and `teardown()` (release resources).
- **A small DAG executor.** `graphlib` ordering plus a `ThreadPoolExecutor`, with
  `when:` / `!when:` conditional branches.
- **Load-time validation.** Unknown node types, typo'd params, references to
  missing nodes, references to undeclared output ports, and cycles fail before
  any expensive (GPU) work starts.
- **Optional server.** FastAPI loads and warms every pipeline at startup.

`topo` ships with **no built-in nodes**. Implementation-specific nodes (a motion
planner, a simulator, scene I/O, ...) live in [`examples/`](examples) as a
pattern to copy. See [`examples/README.md`](examples/README.md).

## Install

```bash
pip install topo
```

Optional extras:

```bash
pip install "topo[dev]"    # pytest, httpx
```

Anything GPU-related (a specific planner, simulator, etc.) is supplied by the
packages that provide those nodes, not by `topo` itself.

## Quick start

Register a node, describe a pipeline, run it:

```python
from topo import Node, NodeParams, Pipeline, PipelineSpec, register


@register("math.double")
class Double(Node):
    class Params(NodeParams):
        factor: int = 2
    outputs = ("value",)

    def run(self, x):
        return {"value": x * self.params.factor}


spec = PipelineSpec.model_validate({
    "name": "demo",
    "nodes": {
        "d": {"type": "math.double", "params": {"factor": 3},
              "inputs": {"x": "$input.x"}},
    },
    "outputs": {"result": "d.value"},
})

p = Pipeline(spec)
p.setup()
print(p.run({"x": 14}))   # {'result': 42}
p.close()
```

The same pipeline as YAML, loaded and overridden from the command line:

```yaml
# demo.yaml
name: demo
max_workers: 4

nodes:
  d:
    type: math.double
    params: {factor: 3}
    inputs: {x: $input.x}

outputs:
  result: d.value
```

```bash
python -m topo demo.yaml --inputs '{"x": 14}' nodes.d.params.factor=10
# or, once installed:  topo demo.yaml --inputs '{"x": 14}'
```

## Running the examples

[`examples/`](examples) is a complete, runnable project to copy from:
implementation-specific nodes (planner, simulator, scene I/O), a demo pipeline,
its tests, and a Dockerfile. None of it is part of the `topo` wheel.

Install the project with its dev dependencies from the repository root:

```bash
pip install -e ".[dev]"
```

The example nodes are not installed as a package, so `topo` must be told where
to import them from. The `TOPO_PLUGINS` environment variable does that (see
[Plugins](#plugins) below), and the `python -m ...` form puts the repository
root on `sys.path`:

```bash
# Run the demo pipeline once. A reachable goal takes the "export" branch:
TOPO_PLUGINS=examples.nodes python -m topo \
    examples/configs/pipelines/plan_and_simulate.yaml \
    --inputs '{"goal":[0.3,0.2,0.4]}'

# A colliding goal takes the "inflate" fallback branch instead:
TOPO_PLUGINS=examples.nodes python -m topo \
    examples/configs/pipelines/plan_and_simulate.yaml \
    --inputs '{"goal":[0.3,0.2,0.05]}'

# Override any value with an OmegaConf dot-list:
TOPO_PLUGINS=examples.nodes python -m topo \
    examples/configs/pipelines/plan_and_simulate.yaml \
    --inputs '{"goal":[0.3,0.2,0.4]}' nodes.simulate.params.min_clearance=0.5
```

Run the tests (`pytest` alone runs the core tests too):

```bash
pytest                 # everything
pytest examples/tests  # just the examples
```

Serve the example pipelines with the FastAPI app:

```bash
TOPO_PLUGINS=examples.nodes PIPELINE_DIR=examples/configs/pipelines \
    python -m uvicorn topo.server:app
# GET  /pipelines
# POST /pipelines/plan_and_simulate/run   {"inputs": {"goal": [0.3, 0.2, 0.4]}}
```

Build the example container (from the repository root):

```bash
docker build -f examples/Dockerfile -t topo-examples .
```

See [`examples/README.md`](examples/README.md) for a breakdown of each node.

## The node model

```python
class Node:
    Params: type[NodeParams]        # Pydantic schema for this node's `params:`
    outputs: tuple[str, ...]        # output ports declared at class level

    def setup(self) -> None: ...    # once per Pipeline, before any run()
    def run(self, **inputs) -> dict[str, Any]: ...   # per request
    def teardown(self) -> None: ... # release resources
```

A node is a stateful object. Do expensive, one-time work in `setup()` — loading
weights, constructing a solver, warming up kernels, capturing a CUDA graph — and
keep `run()` cheap. `run()` must return exactly the keys declared in `outputs`;
the executor checks this. Unknown `params:` keys are errors, so YAML typos are
caught at load time.

## Wiring

Inputs reference `<node>.<port>`, or `$input.<key>` for the request payload.
Prefix a reference with `!` to negate a boolean:

```yaml
nodes:
  plan:
    type: planner.plan
    inputs: {scene: scene.scene, goal: $input.goal}

  export:                       # runs only when the run succeeded
    type: io.export
    inputs: {result: plan.result}
    when: simulate.ok

  fallback:                     # runs only when it did NOT
    type: planner.retry
    inputs: {scene: scene.scene}
    when: "!simulate.ok"
```

## Plugins

Third-party packages can ship nodes without touching core. Advertise them with
an entry point, and `topo` imports them whenever a `Pipeline` is constructed:

```toml
[project.entry-points."topo.nodes"]
my_nodes = "my_pkg.nodes"
```

For projects that are not installed as a package (local scripts, the examples
here — see [Running the examples](#running-the-examples)), list modules in the
`TOPO_PLUGINS` environment variable instead:

```bash
TOPO_PLUGINS=my_project.nodes python -m topo my_pipeline.yaml
```

Importing a module that uses `@register(...)` is all it takes — registration is
a side effect of the import. Module names are resolved against `sys.path`, so
run from the project root, use `python -m ...`, or set `PYTHONPATH`.

## Server

```bash
PIPELINE_DIR=configs/pipelines uvicorn topo.server:app
# GET  /pipelines
# POST /pipelines/{name}/run   {"inputs": {...}}
```

Every `*.yaml` in `PIPELINE_DIR` (default `configs/pipelines`) is loaded and
warmed at startup; a bad config fails the process immediately.

## Layout

| Path                       | Role                                                                 |
| -------------------------- | -------------------------------------------------------------------- |
| `src/topo/config.py`       | Pydantic schema for pipeline YAML; OmegaConf loading + overrides     |
| `src/topo/node.py`         | `Node` base class: `Params`, declared `outputs`, lifecycle hooks     |
| `src/topo/registry.py`     | `@register("type.name")` + entry-point / `TOPO_PLUGINS` loading      |
| `src/topo/executor.py`     | `graphlib` ordering + `ThreadPoolExecutor`; `when:` branches         |
| `src/topo/server.py`       | FastAPI; loads + warms all pipelines at startup                      |
| `src/topo/__main__.py`     | `topo` CLI: run one pipeline from the command line                   |
| `examples/`                | Implementation-specific nodes, pipeline, tests, Dockerfile (not shipped) |
| `tests/`                   | Core executor / validation tests                                     |
| `examples/tests/`          | Tests for the example nodes and pipeline                             |

## What's validated at load time (before any GPU work)

Unknown node types, unknown/typo'd params, references to missing nodes,
references to undeclared output ports, cycles.

## Publishing to PyPI

```bash
uv build                 # or: python -m build
uv publish               # or: twine upload dist/*
```

Bump `version` in `pyproject.toml` for each release. The `examples/` directory
is intentionally excluded from the wheel — only the `topo` package ships.

## Known limits / next steps

- **No loops.** The graph is a DAG. For replan loops, either run the pipeline
  repeatedly from the caller, or add a `flow.loop` node that wraps a sub-pipeline
  and iterates until a condition holds.
- **No if/else merge.** Nodes downstream of a skipped node are skipped too; add
  a `flow.coalesce` node if you need to rejoin branches.
- **One run at a time per pipeline** (a lock), because nodes own GPU state
  (solver buffers, CUDA graphs). For concurrency, build N pipeline instances in
  a pool.
- **Data stays in-process.** Pass large in-memory objects (tensors, arrays)
  directly between nodes; don't serialize them.
- Use Hydra instead of raw OmegaConf if you want config groups / multirun sweeps.

# topotask examples

Implementation-specific nodes and pipelines that show how to use `topotask`. They
are intentionally **not** part of the published `topotask` package — copy what you
need into your own project.

| Node type               | File                      | What it shows                                        |
| ----------------------- | ------------------------- | ---------------------------------------------------- |
| `scene.load`            | `nodes/scene.py`          | A node that produces a scene from a path             |
| `scene.inflate_obstacles` | `nodes/scene.py`        | A "fallback" branch node (`when: "!...."`)            |
| `planner.curobo`        | `nodes/planning.py`       | A stateful GPU node: expensive `setup()`, cheap `run()` |
| `sim.warp`              | `nodes/simulation.py`     | A multi-output node feeding an `if/else` branch       |
| `io.export_trajectory`  | `nodes/io.py`             | A leaf node that serializes an upstream result        |

The planner and simulator are **stubs**. The real cuRobo / Warp calls are left
in comments where they belong.

## Registering the example nodes

`topotask` ships with no built-in nodes, so anything that constructs a `Pipeline`
must import the modules that define them. Two ways:

```bash
# 1. Ad-hoc: list modules in TOPOTASK_PLUGINS (comma-separated).
TOPOTASK_PLUGINS=examples.nodes python -m topotask examples/configs/pipelines/plan_and_simulate.yaml \
    --inputs '{"goal":[0.3,0.2,0.4]}'

# 2. In Python, just import them before building the pipeline.
import examples.nodes  # noqa: F401  (importing registers the node types)
```

A distributable plugin package instead advertises its nodes via an entry point
(see the "Plugins" section of the root `README.md`).

## Run the tests

From the repository root:

```bash
pip install -e ".[dev]"
pytest examples/tests
```

## Serve the example pipeline

```bash
TOPOTASK_PLUGINS=examples.nodes PIPELINE_DIR=examples/configs/pipelines \
    python -m uvicorn topotask.server:app
# POST /pipelines/plan_and_simulate/run {"inputs": {"goal": [0.3, 0.2, 0.4]}}
```

> `TOPOTASK_PLUGINS` resolves module names against `sys.path`, and the `python -m`
> form puts the current directory on it. If you prefer the `uvicorn` / `topotask`
> console scripts, add the repository root explicitly: `PYTHONPATH=. ...`.

## Container

`examples/Dockerfile` builds a CUDA + PyTorch image for these examples. Build it
from the repository root:

```bash
docker build -f examples/Dockerfile -t topotask-examples .
```

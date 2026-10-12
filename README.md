# topo

Reference implementation of: **Pydantic + OmegaConf + a small custom executor**, served via FastAPI.
Planner/sim nodes are stubs; the real cuRobo / Warp calls are in comments where they belong.

## Layout

| File          | Role                                                                                                      |
| ------------- | --------------------------------------------------------------------------------------------------------- |
| `config.py`   | Pydantic schema for pipeline YAML; OmegaConf loading (interpolation, env vars, CLI overrides)             |
| `node.py`     | `Node` base class: `Params` (Pydantic), declared `outputs`, `setup()/run()/teardown()`                    |
| `registry.py` | `@register("type.name")` + entry-point plugins (`topo.nodes`)                                             |
| `executor.py` | `graphlib` ordering + `ThreadPoolExecutor`; `when:` / `!when:` conditional branches; load-time validation |
| `server.py`   | FastAPI; loads + warms all pipelines at startup                                                           |
| `nodes/`      | Example nodes                                                                                             |

## Run

    pip install -e ".[dev]"
    pytest
    python -m topo configs/pipelines/plan_and_simulate.yaml --inputs '{"goal":[0.3,0.2,0.4]}'
    python -m topo configs/pipelines/plan_and_simulate.yaml --inputs '{"goal":[0.3,0.2,0.05]}'   # fallback branch
    uvicorn topo.server:app   # POST /pipelines/plan_and_simulate/run {"inputs": {"goal": [...]}}

## What's validated at load time (before any GPU work)

Unknown node types, unknown/typo'd params, references to missing nodes, references to undeclared output ports, cycles.

## Known limits / next steps

- **No loops.** The graph is a DAG. For replan loops, either run the pipeline repeatedly from the caller, or add a
  `flow.loop` node that wraps a sub-`Pipeline` and iterates until a condition holds.
- **No if/else merge.** Nodes downstream of a skipped node are skipped too; add a `flow.coalesce` node if you need to rejoin branches.
- **One run at a time per pipeline** (a lock), because nodes own GPU state (solver buffers, CUDA graphs).
  For concurrency, build N pipeline instances in a pool.
- **Data stays in-process.** Pass torch tensors / `wp.array`s directly between nodes; don't serialize them.
- Use Hydra instead of raw OmegaConf if you want config groups / multirun sweeps.

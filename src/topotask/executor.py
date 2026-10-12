"""Small DAG executor: graphlib for ordering, a thread pool for parallelism,
`when:` gating for conditional branches. Threads are fine for GPU nodes because
CUDA libraries release the GIL while kernels run."""
from __future__ import annotations

import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from graphlib import CycleError, TopologicalSorter
from typing import Any

from .config import INPUT, PipelineSpec, parse_ref
from .registry import get_node_class, load_plugins


class PipelineError(Exception):
    pass


class Pipeline:
    def __init__(self, spec: PipelineSpec):
        load_plugins()
        self.spec = spec
        self.nodes = {}
        for name, ns in spec.nodes.items():
            cls = get_node_class(ns.type)
            self.nodes[name] = cls(name, cls.Params.model_validate(ns.params))  # validates params
        self.deps = self._build_deps()
        self._validate_ports()
        try:
            TopologicalSorter(self.deps).prepare()  # cycle check
        except CycleError as e:
            raise PipelineError(f"Pipeline {spec.name!r} has a cycle: {e.args[1]}") from None
        self._lock = threading.Lock()  # nodes hold GPU state: one run at a time per pipeline
        self._ready = False

    # ---- load-time validation -------------------------------------------------
    def _build_deps(self) -> dict[str, set[str]]:
        deps: dict[str, set[str]] = {n: set() for n in self.spec.nodes}
        for name, ns in self.spec.nodes.items():
            refs = list(ns.inputs.values()) + ([ns.when] if ns.when else [])
            for r in refs:
                _, src, _ = parse_ref(r)
                if src != INPUT:
                    deps[name].add(src)
        return deps

    def _validate_ports(self) -> None:
        refs = [(n, r) for n, ns in self.spec.nodes.items()
                for r in [*ns.inputs.values(), *([ns.when] if ns.when else [])]]
        refs += [("<outputs>", r) for r in self.spec.outputs.values()]
        for where, r in refs:
            _, src, port = parse_ref(r)
            if src != INPUT and port not in self.nodes[src].outputs:
                raise PipelineError(
                    f"{where}: {r!r} -> node {src!r} has no output {port!r} "
                    f"(declared: {list(self.nodes[src].outputs)})")

    # ---- lifecycle ------------------------------------------------------------
    def setup(self) -> None:
        for n in self.nodes.values():
            n.setup()
        self._ready = True

    def close(self) -> None:
        for n in self.nodes.values():
            n.teardown()
        self._ready = False

    # ---- execution ------------------------------------------------------------
    def run(self, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._ready:
            raise PipelineError("Call setup() before run()")
        with self._lock:
            return self._run(inputs or {})

    def _lookup(self, results: dict[str, dict[str, Any]], ref: str) -> Any:
        negate, src, port = parse_ref(ref)
        val = results[src][port]
        return (not val) if negate else val

    def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        results: dict[str, dict[str, Any]] = {INPUT: inputs}
        skipped: set[str] = set()
        sorter = TopologicalSorter(self.deps)
        sorter.prepare()
        futures: dict[Future, str] = {}

        with ThreadPoolExecutor(self.spec.max_workers) as pool:
            while sorter.is_active():
                for name in sorter.get_ready():
                    ns = self.spec.nodes[name]
                    # Skip if an upstream node was skipped, or the `when` gate is falsy.
                    if self.deps[name] & skipped or (ns.when and not self._lookup(results, ns.when)):
                        skipped.add(name)
                        sorter.done(name)
                        continue
                    kwargs = {k: self._lookup(results, r) for k, r in ns.inputs.items()}
                    futures[pool.submit(self._call, name, kwargs)] = name
                if futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for f in done:
                        name = futures.pop(f)
                        results[name] = f.result()   # re-raises node errors
                        sorter.done(name)

        out = {}
        for key, ref in self.spec.outputs.items():
            _, src, port = parse_ref(ref)
            out[key] = None if src in skipped else results[src][port]
        return out

    def _call(self, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        node = self.nodes[name]
        try:
            res = node.run(**kwargs)
        except Exception as e:
            raise PipelineError(f"Node {name!r} ({node.type_name}) failed: {e}") from e
        if set(res) != set(node.outputs):
            raise PipelineError(f"Node {name!r} returned {sorted(res)}, declared {sorted(node.outputs)}")
        return res

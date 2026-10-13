"""Small DAG executor: graphlib for ordering, a thread pool for parallelism,
`when:` gating for conditional branches. Threads are fine for GPU nodes because
CUDA libraries release the GIL while kernels run."""
from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import Enum
from graphlib import CycleError, TopologicalSorter
from typing import Any

from .config import INPUT, PipelineSpec, parse_ref
from .registry import get_node_class, load_plugins


class PipelineError(Exception):
    pass


class NodeStatus(str, Enum):
    """Outcome of a single node in a run, used by the analytics/report output."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"    # `when:` was falsy, or an upstream node was skipped
    NOT_RUN = "not_run"    # never reached (an earlier node failed)


@dataclass
class RunReport:
    """Per-node outcome of one run. Always available as ``Pipeline.last_report``.

    ``run()`` still raises on failure for backward compatibility; ``run_report()``
    returns this instead so callers (the CLI, analytics) can render a report even
    when a node failed.
    """
    pipeline: str
    statuses: dict[str, NodeStatus] = field(default_factory=dict)
    durations: dict[str, float] = field(default_factory=dict)   # per node, seconds
    errors: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    duration: float = 0.0                                       # wall time of the whole run


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
        self.last_report: RunReport | None = None

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
        """Run once and return the resolved outputs. Raises on node failure.

        The report is still stored on ``self.last_report`` before raising, so a
        caller can render analytics for a failed run.
        """
        if not self._ready:
            raise PipelineError("Call setup() before run()")
        with self._lock:
            report = self._execute(inputs or {})
        self.last_report = report
        if not report.ok:
            raise PipelineError(next(iter(report.errors.values())))
        return report.outputs

    def run_report(self, inputs: dict[str, Any] | None = None) -> RunReport:
        """Like ``run()`` but returns a :class:`RunReport` and never raises on a
        node failure (the error is captured in the report instead)."""
        if not self._ready:
            raise PipelineError("Call setup() before run_report()")
        with self._lock:
            report = self._execute(inputs or {})
        self.last_report = report
        return report

    def _lookup(self, results: dict[str, dict[str, Any]], ref: str) -> Any:
        negate, src, port = parse_ref(ref)
        val = results[src][port]
        return (not val) if negate else val

    def _execute(self, inputs: dict[str, Any]) -> RunReport:
        report = RunReport(pipeline=self.spec.name)
        wall_start = time.perf_counter()
        results: dict[str, dict[str, Any]] = {INPUT: inputs}
        skipped: set[str] = set()
        sorter = TopologicalSorter(self.deps)
        sorter.prepare()
        futures: dict[Future, str] = {}
        started: dict[str, float] = {}
        failed = False

        pool = ThreadPoolExecutor(self.spec.max_workers)
        try:
            while sorter.is_active():
                for name in sorter.get_ready():
                    ns = self.spec.nodes[name]
                    # Skip if an upstream node was skipped, or the `when` gate is falsy.
                    if self.deps[name] & skipped or (ns.when and not self._lookup(results, ns.when)):
                        skipped.add(name)
                        report.statuses[name] = NodeStatus.SKIPPED
                        sorter.done(name)
                        continue
                    kwargs = {k: self._lookup(results, r) for k, r in ns.inputs.items()}
                    started[name] = time.perf_counter()
                    futures[pool.submit(self._call, name, kwargs)] = name
                if futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    for f in done:
                        name = futures.pop(f)
                        report.durations[name] = time.perf_counter() - started[name]
                        try:
                            results[name] = f.result()   # re-raises node errors
                        except Exception as e:
                            report.statuses[name] = NodeStatus.FAILED
                            report.errors[name] = str(e)
                            failed = True
                            continue        # don't unlock downstream after a failure
                        report.statuses[name] = NodeStatus.SUCCEEDED
                        if not failed:
                            sorter.done(name)
                if failed:
                    break
        finally:
            # Cancel anything still queued; let in-flight nodes finish so the
            # report reflects what actually ran.
            pool.shutdown(wait=True, cancel_futures=True)

        # In-flight nodes that completed while we were shutting down.
        for f, name in futures.items():
            if f.cancelled():
                continue
            report.durations.setdefault(name, time.perf_counter() - started.get(name, 0.0))
            try:
                results[name] = f.result()
                report.statuses.setdefault(name, NodeStatus.SUCCEEDED)
            except Exception as e:
                report.statuses.setdefault(name, NodeStatus.FAILED)
                report.errors.setdefault(name, str(e))

        # Anything without an explicit status never ran.
        for name in self.spec.nodes:
            report.statuses.setdefault(name, NodeStatus.NOT_RUN)

        out: dict[str, Any] = {}
        for key, ref in self.spec.outputs.items():
            _, src, port = parse_ref(ref)
            if src == INPUT:
                out[key] = results[INPUT].get(port)
            elif report.statuses.get(src) == NodeStatus.SUCCEEDED:
                out[key] = results[src][port]
            else:
                out[key] = None
        report.outputs = out
        report.ok = not failed
        report.duration = time.perf_counter() - wall_start
        return report

    def _call(self, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        node = self.nodes[name]
        try:
            res = node.run(**kwargs)
        except Exception as e:
            raise PipelineError(f"Node {name!r} ({node.type_name}) failed: {e}") from e
        if set(res) != set(node.outputs):
            raise PipelineError(f"Node {name!r} returned {sorted(res)}, declared {sorted(node.outputs)}")
        return res

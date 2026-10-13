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

from .config import (INPUT, AllOf, AnyOf, Condition, Leaf, PipelineSpec,
                     iter_condition_leaves, parse_ref)
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
        self.data_deps: dict[str, set[str]] = {n: set() for n in self.spec.nodes}
        self.cond_deps: dict[str, set[str]] = {n: set() for n in self.spec.nodes}
        for name, ns in self.spec.nodes.items():
            for r in ns.inputs.values():
                _, src, _ = parse_ref(r)
                if src != INPUT:
                    self.data_deps[name].add(src)
            for leaf in iter_condition_leaves(ns.when):
                if leaf.node != INPUT:
                    self.cond_deps[name].add(leaf.node)
        return {n: self.data_deps[n] | self.cond_deps[n] for n in self.spec.nodes}

    def _validate_ports(self) -> None:
        refs = [(n, r) for n, ns in self.spec.nodes.items()
                for r in ns.inputs.values()]
        refs += [(n, f"{leaf.node}.{leaf.port}") for n, ns in self.spec.nodes.items()
                 for leaf in iter_condition_leaves(ns.when)]
        refs += [("<outputs>", r) for r in self.spec.outputs.values()]
        for where, r in refs:
            _, src, port = parse_ref(r)
            if src == INPUT:
                continue
            head = port.split(".", 1)[0]   # a.b.c addresses output 'a' then keys
            if head not in self.nodes[src].outputs:
                raise PipelineError(
                    f"{where}: {r!r} -> node {src!r} has no output {head!r} "
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

    def _lookup_value(self, results: dict[str, dict[str, Any]], node: str, port: str) -> Any:
        """Resolve ``node.port``; dots in ``port`` walk into a dict output."""
        value = results[node]
        if port in value:
            return value[port]
        for part in port.split("."):
            value = value[part]
        return value

    def _lookup(self, results: dict[str, dict[str, Any]], ref: str) -> Any:
        negate, src, port = parse_ref(ref)
        val = self._lookup_value(results, src, port)
        return (not val) if negate else val

    @staticmethod
    def _compare(left: Any, op: str, right: Any, node: str, port: str) -> bool:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        try:
            if op == "<":
                return left < right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            return left >= right
        except TypeError as e:
            raise PipelineError(
                f"Cannot compare {node}.{port} ({left!r}) {op} {right!r}: {e}") from None

    def _eval_condition(self, cond: Condition | None,
                        results: dict[str, dict[str, Any]],
                        skipped: set[str]) -> bool | None:
        """Three-valued gate evaluation: True, False, or None (unavailable).

        A leaf whose source node was skipped (or never produced a value) is
        *unknown*. ``all`` is True only if every child is True and False if any
        is False; ``any`` is True if any child is True. Unknown propagates, so a
        single unavailable gate skips the node, while an ``any`` can still be
        satisfied by a sibling branch.
        """
        if cond is None:
            return True
        if isinstance(cond, AllOf):
            unknown = False
            for child in cond.conditions:
                value = self._eval_condition(child, results, skipped)
                if value is False:
                    return False
                unknown |= value is None
            return None if unknown else True
        if isinstance(cond, AnyOf):
            unknown = False
            for child in cond.conditions:
                value = self._eval_condition(child, results, skipped)
                if value is True:
                    return True
                unknown |= value is None
            return None if unknown else False

        leaf: Leaf = cond
        if leaf.node != INPUT and (leaf.node in skipped or leaf.node not in results):
            return None
        value = self._lookup_value(results, leaf.node, leaf.port)
        if leaf.op is None:
            result: bool | None = bool(value)
        else:
            result = self._compare(value, leaf.op, leaf.value, leaf.node, leaf.port)
        return (not result) if leaf.negate else result

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
                    # Skip if a data input came from a skipped node; otherwise
                    # evaluate the `when` gate (skipped gate sources count as
                    # unavailable and fail an `all`, but not an `any`).
                    gate_ok = self._eval_condition(ns.when, results, skipped) is True
                    if self.data_deps[name] & skipped or not gate_ok:
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

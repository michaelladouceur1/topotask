"""Analytics output for a pipeline: draw its DAG, and draw a run's results.

The text emitters (:func:`to_dot`, :func:`to_mermaid`) are dependency-free and
live in the core package. Rasterizing to an image needs the optional
``graphviz`` dependency and the Graphviz ``dot`` executable::

    pip install "topotask[viz]"

Both images come from the same DOT source and differ only in node fill/legend,
so Graphviz lays them out identically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import INPUT, PipelineSpec, parse_ref

if TYPE_CHECKING:
    from .executor import RunReport

# Status -> (fill, border). Kept in sync with NodeStatus values in executor.py.
_STATUS_STYLE: dict[str, tuple[str, str]] = {
    "succeeded": ("#c8e6c9", "#2e7d32"),   # green
    "failed": ("#ffcdd2", "#c62828"),      # red
    "skipped": ("#e0e0e0", "#9e9e9e"),     # grey
    "not_run": ("#ffffff", "#9e9e9e"),     # white
}
_STATUS_ORDER = ("succeeded", "failed", "skipped", "not_run")


def _q(value: Any) -> str:
    """Quote a DOT string. Graphviz treats a literal ``\\n`` as a line break."""
    return '"' + str(value).replace('"', '\\"') + '"'


def _status(report: "RunReport | None", name: str) -> str | None:
    if report is None:
        return None
    st = report.statuses.get(name)
    return st.value if st is not None else "not_run"


def _refs(spec: PipelineSpec) -> list[str]:
    refs = [r for ns in spec.nodes.values() for r in ns.inputs.values()]
    refs += [ns.when for ns in spec.nodes.values() if ns.when]
    refs += list(spec.outputs.values())
    return refs


def _input_keys(spec: PipelineSpec) -> list[str]:
    keys = set()
    for r in _refs(spec):
        _, src, port = parse_ref(r)
        if src == INPUT:
            keys.add(port)
    return sorted(keys)


def to_dot(spec: PipelineSpec, report: "RunReport | None" = None) -> str:
    """Graphviz DOT for ``spec``. Pass a :class:`RunReport` to colour nodes by
    run status and add a legend."""
    lines = [
        f"digraph {_q(spec.name)} {{",
        "  rankdir=LR;",
        f"  label={_q(spec.name)};",
        "  labelloc=t;",
        '  fontname="Helvetica";',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica",'
        ' fillcolor="#e3f2fd", color="#1565c0"];',
        '  edge [fontname="Helvetica", fontsize=10, color="#546e7a"];',
    ]

    keys = _input_keys(spec)
    if keys:
        label = "Inputs\\n(" + ", ".join(keys) + ")"
        lines.append(f"  {_q(INPUT)} [label={_q(label)},"
                     ' shape=note, fillcolor="#fff9c4", color="#f9a825"];')

    for name, ns in spec.nodes.items():
        label = name + "\\n" + ns.type
        attrs = [f"label={_q(label)}"]
        st = _status(report, name)
        if st is not None:
            fill, border = _STATUS_STYLE[st]
            attrs += [f"fillcolor={_q(fill)}", f"color={_q(border)}"]
            if st in ("skipped", "not_run"):
                attrs.append('style="rounded,filled,dashed"')
        lines.append(f"  {_q(name)} [{', '.join(attrs)}];")

    for name, ns in spec.nodes.items():
        for kwarg, ref in ns.inputs.items():
            _, src, port = parse_ref(ref)
            tail = _q(INPUT) if src == INPUT else _q(src)
            lines.append(f"  {tail} -> {_q(name)} [label={_q(kwarg)}];")
        if ns.when:
            negate, src, port = parse_ref(ns.when)
            tail = _q(INPUT) if src == INPUT else _q(src)
            gate = f"when {'!' if negate else ''}{port}"
            lines.append(f"  {tail} -> {_q(name)} [label={_q(gate)}, style=dashed,"
                         ' color="#9e9e9e"];')

    for i, (key, ref) in enumerate(spec.outputs.items()):
        _, src, port = parse_ref(ref)
        tail = _q(INPUT) if src == INPUT else _q(src)
        oid = f"__out__{i}"
        lines.append(f"  {_q(oid)} [label={_q(key)}, shape=oval,"
                     ' fillcolor="#f3e5f5", color="#6a1b9a"];')
        lines.append(f"  {tail} -> {_q(oid)} [style=dotted, label={_q(port)}];")

    if report is not None:
        lines.append("  subgraph cluster_legend {")
        lines.append('    label="Run status"; fontsize=10; style=dashed; color="#bdbdbd";')
        for st in _STATUS_ORDER:
            fill, border = _STATUS_STYLE[st]
            lines.append(f"    legend_{st} [label={_q(st)}, shape=box,"
                         f' style="rounded,filled", fillcolor={_q(fill)},'
                         f" color={_q(border)}];")
        same = " ".join(f"legend_{st}" for st in _STATUS_ORDER)
        lines.append(f"    {{rank=same; {same};}}")
        lines.append("  }")

    lines.append("}")
    return "\n".join(lines) + "\n"


def to_mermaid(spec: PipelineSpec, report: "RunReport | None" = None) -> str:
    """Mermaid flowchart for ``spec`` — handy in markdown/PRs without a renderer."""
    lines = ["flowchart LR"]
    ids = {name: f"n{i}" for i, name in enumerate(spec.nodes)}
    keys = _input_keys(spec)
    if keys:
        lines.append(f'  inp["Inputs<br/>{"<br/>".join(keys)}"]')

    for name, ns in spec.nodes.items():
        lines.append(f'  {ids[name]}["{name}<br/>{ns.type}"]')

    for name, ns in spec.nodes.items():
        for kwarg, ref in ns.inputs.items():
            _, src, port = parse_ref(ref)
            tail = "inp" if src == INPUT else ids[src]
            lines.append(f"  {tail} -->|{kwarg}| {ids[name]}")
        if ns.when:
            negate, src, port = parse_ref(ns.when)
            tail = "inp" if src == INPUT else ids[src]
            gate = f"when {'!' if negate else ''}{port}"
            lines.append(f"  {tail} -.->|{gate}| {ids[name]}")

    for i, (key, ref) in enumerate(spec.outputs.items()):
        _, src, port = parse_ref(ref)
        tail = "inp" if src == INPUT else ids[src]
        lines.append(f'  o{i}(["{key}"])')
        lines.append(f"  {tail} -.->|{port}| o{i}")

    if report is not None:
        for st, (fill, border) in _STATUS_STYLE.items():
            lines.append(f"  classDef {st} fill:{fill},stroke:{border};")
        for name in spec.nodes:
            lines.append(f"  class {ids[name]} {_status(report, name)};")

    return "\n".join(lines) + "\n"


def render(spec: PipelineSpec, out_path: str | Path, report: "RunReport | None" = None,
           fmt: str | None = None) -> Path:
    """Rasterize ``spec`` (optionally with run results) to ``out_path``.

    Requires the optional ``graphviz`` package and the ``dot`` executable.
    """
    try:
        import graphviz
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Rendering images needs the optional dependency; install it with: "
            "pip install 'topotask[viz]'"
        ) from e

    out_path = Path(out_path)
    if out_path.suffix:
        fmt = fmt or out_path.suffix.lstrip(".")
        stem = out_path.with_suffix("")
    else:
        fmt = fmt or "png"
        out_path = out_path.with_suffix("." + fmt)
        stem = out_path.with_suffix("")

    source = graphviz.Source(to_dot(spec, report), format=fmt)
    try:
        source.render(filename=str(stem), cleanup=True)
    except graphviz.ExecutableNotFound as e:  # pragma: no cover - environment specific
        raise RuntimeError(
            "The Graphviz 'dot' executable was not found on PATH; install Graphviz "
            "to render images (the text output via to_dot()/to_mermaid() needs no "
            "executable)."
        ) from e
    return out_path


def write_report_json(report: "RunReport", path: str | Path) -> Path:
    """Serialize a :class:`RunReport` to JSON (machine-readable companion to the images)."""
    path = Path(path)
    data = {
        "pipeline": report.pipeline,
        "ok": report.ok,
        "statuses": {name: status.value for name, status in report.statuses.items()},
        "durations": report.durations,
        "errors": report.errors,
        "outputs": report.outputs,
    }
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    return path


def render_all(spec: PipelineSpec, out_dir: str | Path, report: "RunReport | None" = None,
               fmt: str = "png") -> list[Path]:
    """Write ``graph.<fmt>`` (structure), and if a report is given,
    ``results.<fmt>`` plus ``report.json``, into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [render(spec, out_dir / f"graph.{fmt}", fmt=fmt)]
    if report is not None:
        written.append(render(spec, out_dir / f"results.{fmt}", report=report, fmt=fmt))
        written.append(write_report_json(report, out_dir / "report.json"))
    return written

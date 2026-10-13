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
_STATUS_LABEL = {"not_run": "not run"}     # friendlier than the enum value

# ``dot``/``mmd`` are text and need no renderer; the rest go through Graphviz.
_TEXT_FORMATS = {"dot", "mmd"}
_GRAPHVIZ_FORMATS = {
    "bmp", "eps", "gif", "jpeg", "jpg", "json", "pdf", "plain",
    "png", "ps", "svg", "tif", "tiff", "webp",
}


def _q(value: Any) -> str:
    """Quote a DOT string. Graphviz treats a literal ``\\n`` as a line break."""
    return '"' + str(value).replace('"', '\\"') + '"'


def _html(value: Any) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _status(report: "RunReport | None", name: str) -> str | None:
    if report is None:
        return None
    st = report.statuses.get(name)
    return st.value if st is not None else "not_run"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 1.0:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.2f} s"


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


def _node_extra(report: "RunReport | None", name: str) -> str | None:
    """Third label line: the node's duration, or its status if it never ran."""
    if report is None:
        return None
    if name in report.durations:
        return _fmt_duration(report.durations[name])
    return _STATUS_LABEL.get(_status(report, name), _status(report, name))


def _graph_label(spec: PipelineSpec, report: "RunReport | None") -> str:
    """HTML graph label: pipeline name, total time, and (with a report) a legend.

    Keeping the legend in the graph label — rather than a cluster — means it
    never perturbs node layout, so the structure and results images line up.
    """
    title = f"<B>{_html(spec.name)}</B>"
    if report is None:
        return f"<{title}>"

    total = f"total: {_fmt_duration(report.duration)}"
    cells = "".join(
        f'<TD BGCOLOR="{_STATUS_STYLE[s][0]}" BORDER="1" CELLPADDING="3">'
        f"{_html(_STATUS_LABEL.get(s, s))}</TD>"
        for s in _STATUS_ORDER
    )
    return (
        "<"
        '<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="2">'
        f'<TR><TD ALIGN="CENTER">{title}</TD></TR>'
        f'<TR><TD ALIGN="CENTER">{_html(total)}</TD></TR>'
        f'<TR><TD ALIGN="CENTER"><TABLE BORDER="0" CELLBORDER="0" CELLSPACING="3">'
        f"<TR>{cells}</TR></TABLE></TD></TR>"
        "</TABLE>"
        ">"
    )


def to_dot(spec: PipelineSpec, report: "RunReport | None" = None) -> str:
    """Graphviz DOT for ``spec``. Pass a :class:`RunReport` to colour nodes by
    run status, show per-node and total timings, and add a legend."""
    lines = [
        f"digraph {_q(spec.name)} {{",
        "  rankdir=LR;",
        "  ordering=out;",
        "  ranksep=0.9;",
        "  nodesep=0.45;",
        "  splines=ortho;",
        "  labelloc=t;",
        "  fontname=\"Helvetica\";",
        f"  label={_graph_label(spec, report)};",
        # Uniform boxes keep nodes aligned into even columns.
        '  node [shape=box, style="rounded,filled", fontname="Helvetica",'
        ' fontsize=11, width=2.2, height=0.9, margin="0.12,0.08",'
        ' fillcolor="#e3f2fd", color="#1565c0"];',
        '  edge [fontname="Helvetica", fontsize=10, color="#546e7a"];',
    ]

    keys = _input_keys(spec)
    if keys:
        label = "Inputs\\n(" + ", ".join(keys) + ")"
        lines.append(f"  {_q(INPUT)} [label={_q(label)},"
                     ' shape=box, fillcolor="#fff9c4", color="#f9a825"];')

    for name, ns in spec.nodes.items():
        label_lines = [name, ns.type]
        extra = _node_extra(report, name)
        if extra is not None:
            label_lines.append(extra)
        label = "\\n".join(label_lines)
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
            lines.append(f"  {tail} -> {_q(name)} [xlabel={_q(kwarg)}];")
        if ns.when:
            negate, src, port = parse_ref(ns.when)
            tail = _q(INPUT) if src == INPUT else _q(src)
            gate = f"when {'!' if negate else ''}{port}"
            # Gates are control flow, not data flow: don't let them constrain ranks.
            lines.append(f"  {tail} -> {_q(name)} [xlabel={_q(gate)}, style=dashed,"
                         ' color="#9e9e9e", constraint=false];')

    for i, (key, ref) in enumerate(spec.outputs.items()):
        _, src, port = parse_ref(ref)
        tail = _q(INPUT) if src == INPUT else _q(src)
        oid = f"__out__{i}"
        lines.append(f"  {_q(oid)} [label={_q(key)}, peripheries=2,"
                     ' fillcolor="#f3e5f5", color="#6a1b9a"];')
        lines.append(f"  {tail} -> {_q(oid)} [style=dotted, xlabel={_q(port)}];")

    lines.append("}")
    return "\n".join(lines) + "\n"


def to_mermaid(spec: PipelineSpec, report: "RunReport | None" = None) -> str:
    """Mermaid flowchart for ``spec`` — handy in markdown/PRs without a renderer."""
    lines = ["flowchart LR"]
    if report is not None:
        lines = [f"---\ntitle: {spec.name} (total {_fmt_duration(report.duration)})\n---",
                 "flowchart LR"]
    ids = {name: f"n{i}" for i, name in enumerate(spec.nodes)}
    keys = _input_keys(spec)
    if keys:
        lines.append(f'  inp["Inputs<br/>{"<br/>".join(keys)}"]')

    for name, ns in spec.nodes.items():
        parts = [name, ns.type]
        extra = _node_extra(report, name)
        if extra is not None:
            parts.append(extra)
        lines.append(f'  {ids[name]}["{"<br/>".join(parts)}"]')

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
    """Write ``spec`` (optionally with run results) to ``out_path``.

    ``fmt`` defaults to the path's suffix, else ``png``. ``dot`` and ``mmd`` write
    the text source directly and need no renderer; every other format rasterizes
    via Graphviz (the optional ``graphviz`` package + the ``dot`` executable).
    """
    out_path = Path(out_path)
    fmt = (fmt or (out_path.suffix.lstrip(".") if out_path.suffix else "png")).lower()
    if fmt == "mermaid":            # accept the friendly alias
        fmt = "mmd"

    if fmt in _TEXT_FORMATS:
        text = (to_dot if fmt == "dot" else to_mermaid)(spec, report)
        out_path = out_path.with_suffix("." + fmt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text)
        return out_path

    if fmt not in _GRAPHVIZ_FORMATS:
        raise ValueError(
            f"Unknown viz format {fmt!r}; use one of "
            f"{sorted(_TEXT_FORMATS | _GRAPHVIZ_FORMATS)}."
        )

    try:
        import graphviz
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Rendering images needs the optional dependency; install it with: "
            "pip install 'topotask[viz]' (or: uv sync --extra viz)"
        ) from e

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
        "duration": report.duration,
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

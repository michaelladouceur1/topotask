"""Run a pipeline once from the command line: ``python -m topotask <config.yaml>``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_pipeline_spec
from .executor import Pipeline


def _write_viz(spec, out: Path, label: str, fmt: str, report=None) -> None:
    """Render one analytics artifact, never letting a viz failure kill the run.

    On an image failure, fall back to writing the ``.dot`` source so ``--viz-dir``
    still leaves something behind.
    """
    from .viz import render

    try:
        render(spec, out / f"{label}.{fmt}", report=report, fmt=fmt)
        return
    except Exception as e:
        print(f"warning: could not write {label}.{fmt}: {e}", file=sys.stderr)
    if fmt not in ("dot", "mmd"):
        try:
            render(spec, out / f"{label}.dot", report=report, fmt="dot")
            print(f"warning: wrote {label}.dot instead", file=sys.stderr)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="topotask",
        description="Run a topotask pipeline once.",
        epilog="Overrides are OmegaConf dot-list assignments, e.g. "
               "nodes.plan.params.max_attempts=8",
    )
    ap.add_argument("config")
    ap.add_argument("--inputs", default="{}")
    ap.add_argument("--viz-dir", default=None,
                    help="Write analytics into this directory: graph.<fmt> (the config's "
                         "DAG) and, after the run, results.<fmt> + report.json.")
    ap.add_argument("--viz-format", default="png", choices=["png", "svg", "pdf", "dot", "mmd"],
                    help="Output format for --viz-dir (default: png). 'dot' and 'mmd' are "
                         "text and need no renderer.")
    # Collect overrides via parse_known_args so they can appear before or after
    # --inputs (a `nargs="*"` positional cannot be mixed with an optional here).
    a, extra = ap.parse_known_args(argv)
    bad = [x for x in extra if "=" not in x]
    if bad:
        ap.error(f"unrecognized arguments: {' '.join(bad)}")

    spec = load_pipeline_spec(a.config, extra)
    p = Pipeline(spec)
    p.setup()
    try:
        inputs = json.loads(a.inputs)
        if a.viz_dir:
            from .viz import write_report_json

            out = Path(a.viz_dir)
            out.mkdir(parents=True, exist_ok=True)
            # Structure first, so it exists even if a node crashes; analytics is
            # best-effort and never blocks the run.
            _write_viz(spec, out, "graph", a.viz_format)
            report = p.run_report(inputs)
            write_report_json(report, out / "report.json")   # pure JSON, always writable
            _write_viz(spec, out, "results", a.viz_format, report)
            print(json.dumps(report.outputs, indent=2))
            for name, msg in report.errors.items():
                print(f"error: {msg}", file=sys.stderr)
            if not report.ok:
                sys.exit(1)
        else:
            print(json.dumps(p.run(inputs), indent=2))
    finally:
        p.close()


if __name__ == "__main__":
    main()

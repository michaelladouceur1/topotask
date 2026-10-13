"""Run a pipeline once from the command line: ``python -m topotask <config.yaml>``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_pipeline_spec
from .executor import Pipeline


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
    ap.add_argument("--viz-format", default="png", choices=["png", "svg", "pdf"],
                    help="Image format for --viz-dir (default: png).")
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
            from .viz import render, write_report_json

            out = Path(a.viz_dir)
            # Draw the structure before running, so it exists even if a node crashes.
            render(spec, out / f"graph.{a.viz_format}", fmt=a.viz_format)
            report = p.run_report(inputs)
            render(spec, out / f"results.{a.viz_format}", report=report, fmt=a.viz_format)
            write_report_json(report, out / "report.json")
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

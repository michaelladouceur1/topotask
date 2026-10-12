"""Run a pipeline once from the command line: ``python -m topo <config.yaml>``."""
from __future__ import annotations

import argparse
import json

from .config import load_pipeline_spec
from .executor import Pipeline


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="topo",
        description="Run a topo pipeline once.",
        epilog="Overrides are OmegaConf dot-list assignments, e.g. "
               "nodes.plan.params.max_attempts=8",
    )
    ap.add_argument("config")
    ap.add_argument("--inputs", default="{}")
    # Collect overrides via parse_known_args so they can appear before or after
    # --inputs (a `nargs="*"` positional cannot be mixed with an optional here).
    a, extra = ap.parse_known_args(argv)
    bad = [x for x in extra if "=" not in x]
    if bad:
        ap.error(f"unrecognized arguments: {' '.join(bad)}")

    p = Pipeline(load_pipeline_spec(a.config, extra))
    p.setup()
    try:
        print(json.dumps(p.run(json.loads(a.inputs)), indent=2))
    finally:
        p.close()


if __name__ == "__main__":
    main()

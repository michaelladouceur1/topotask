"""Run a pipeline once from the command line: ``python -m topo <config.yaml>``."""
from __future__ import annotations

import argparse
import json

from .config import load_pipeline_spec
from .executor import Pipeline


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="topo", description="Run a topo pipeline once.")
    ap.add_argument("config")
    ap.add_argument("--inputs", default="{}")
    ap.add_argument("overrides", nargs="*", help="OmegaConf dotlist overrides")
    a = ap.parse_args(argv)

    p = Pipeline(load_pipeline_spec(a.config, a.overrides))
    p.setup()
    try:
        print(json.dumps(p.run(json.loads(a.inputs)), indent=2))
    finally:
        p.close()


if __name__ == "__main__":
    main()

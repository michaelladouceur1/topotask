"""python -m topo configs/pipelines/plan_and_simulate.yaml --inputs '{"goal":[0.3,0.2,0.4]}' \
       nodes.plan.params.max_attempts=8"""
import argparse
import json

from .config import load_pipeline_spec
from .executor import Pipeline

ap = argparse.ArgumentParser()
ap.add_argument("config")
ap.add_argument("--inputs", default="{}")
ap.add_argument("overrides", nargs="*", help="OmegaConf dotlist overrides")
a = ap.parse_args()

p = Pipeline(load_pipeline_spec(a.config, a.overrides))
p.setup()
try:
    print(json.dumps(p.run(json.loads(a.inputs)), indent=2))
finally:
    p.close()

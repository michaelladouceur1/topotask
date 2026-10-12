"""FastAPI server: loads every pipeline in PIPELINE_DIR at startup, keeps them warm."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import load_pipeline_spec
from .executor import Pipeline, PipelineError

PIPELINE_DIR = Path(os.environ.get("PIPELINE_DIR", "configs/pipelines"))
pipelines: dict[str, Pipeline] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    for path in sorted(PIPELINE_DIR.glob("*.yaml")):
        p = Pipeline(load_pipeline_spec(path))   # fails fast on a bad config
        p.setup()                                # load models / warm up once
        pipelines[p.spec.name] = p
    yield
    for p in pipelines.values():
        p.close()


app = FastAPI(title="topotask", lifespan=lifespan)


class RunRequest(BaseModel):
    inputs: dict[str, Any] = {}


@app.get("/pipelines")
def list_pipelines():
    return {name: p.spec.model_dump() for name, p in pipelines.items()}


@app.post("/pipelines/{name}/run")
def run_pipeline(name: str, req: RunRequest):   # sync def => FastAPI runs it in a worker thread
    if name not in pipelines:
        raise HTTPException(404, f"No pipeline {name!r}")
    try:
        return pipelines[name].run(req.inputs)
    except PipelineError as e:
        raise HTTPException(500, str(e))

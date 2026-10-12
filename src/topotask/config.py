"""Pydantic schema for pipeline YAML + OmegaConf loading."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, model_validator

INPUT = "$input"  # pseudo-node holding the request's inputs


def parse_ref(ref: str) -> tuple[bool, str, str]:
    """'!node.port' -> (negate, node, port). '$input.key' refers to request inputs."""
    negate = ref.startswith("!")
    ref = ref.lstrip("!")
    node, sep, port = ref.partition(".")
    if not sep or not node or not port:
        raise ValueError(f"Bad reference {ref!r}; expected '<node>.<port>'")
    return negate, node, port


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    params: dict[str, Any] = {}
    inputs: dict[str, str] = {}      # kwarg name -> "node.port" | "$input.key"
    when: str | None = None          # "node.port" or "!node.port"; falsy => node skipped


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    max_workers: int = 4
    nodes: dict[str, NodeSpec]
    outputs: dict[str, str] = {}

    @model_validator(mode="after")
    def _check_refs(self):
        refs = [r for n in self.nodes.values() for r in n.inputs.values()]
        refs += [n.when for n in self.nodes.values() if n.when]
        refs += list(self.outputs.values())
        for r in refs:
            _, node, _ = parse_ref(r)
            if node != INPUT and node not in self.nodes:
                raise ValueError(f"Reference {r!r} points to unknown node {node!r}")
        return self


def load_pipeline_spec(path: str | Path, overrides: list[str] | None = None) -> PipelineSpec:
    """Load YAML via OmegaConf (interpolation, ${oc.env:...}, dotlist overrides),
    then validate with Pydantic. Overrides look like: nodes.plan.params.max_attempts=8"""
    cfg = OmegaConf.load(path)
    if overrides:
        cfg = OmegaConf.merge(cfg, OmegaConf.from_dotlist(overrides))
    return PipelineSpec.model_validate(OmegaConf.to_container(cfg, resolve=True))

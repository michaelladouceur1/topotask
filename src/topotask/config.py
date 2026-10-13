"""Pydantic schema for pipeline YAML + OmegaConf loading."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Union

from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

INPUT = "$input"  # pseudo-node holding the request's inputs


def parse_ref(ref: str) -> tuple[bool, str, str]:
    """'!node.port' -> (negate, node, port). '$input.key' refers to request inputs."""
    negate = ref.startswith("!")
    ref = ref.lstrip("!")
    node, sep, port = ref.partition(".")
    if not sep or not node or not port:
        raise ValueError(f"Bad reference {ref!r}; expected '<node>.<port>'")
    return negate, node, port


# ---- when conditions --------------------------------------------------------
#
# A ``when`` gate is a small boolean expression tree over nodes' outputs:
#
#   when: simulate.collision_free                 # single truthy port
#   when: "!simulate.collision_free"              # negated
#   when: plan.steps > 3                          # comparison against a literal
#   when: [simulate.collision_free, plan.steps > 3]   # list => ALL (AND)
#   when: {any: [a.ok, "!b.ok"]}                  # explicit ANY / ALL, nestable
#
# The list form is AND; combinators ``{all: [...]}`` and ``{any: [...]}`` make
# the logic explicit and can be nested.

_LEAF_RE = re.compile(
    r"^\s*(?P<neg>!)?\s*(?P<ref>[^\s<>=!]+)\s*"
    r"(?:(?P<op>==|!=|<=|>=|<|>)\s*(?P<val>.+?))?\s*$"
)


@dataclass(frozen=True)
class Leaf:
    """A single condition: ``[!]node.port [op literal]``."""
    negate: bool
    node: str
    port: str
    op: str | None = None
    value: Any = None


@dataclass(frozen=True)
class AllOf:
    """Every child condition must hold (logical AND)."""
    conditions: tuple["Condition", ...]


@dataclass(frozen=True)
class AnyOf:
    """At least one child condition must hold (logical OR)."""
    conditions: tuple["Condition", ...]


Condition = Union[Leaf, AllOf, AnyOf]


def _parse_literal(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        return text[1:-1]
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    raise ValueError(
        f"Bad literal {text!r} in a when condition; use a number, true/false, "
        "null, or a quoted string.")


def _parse_leaf(text: str) -> Leaf:
    m = _LEAF_RE.match(text)
    if not m:
        raise ValueError(
            f"Bad when condition {text!r}; expected '[!]<node>.<port> [op value]'")
    negate, node, port = parse_ref(("!" if m["neg"] else "") + m["ref"])
    op = m["op"]
    return Leaf(negate=negate, node=node, port=port, op=op,
                value=_parse_literal(m["val"]) if op else None)


def parse_condition(cond: Any) -> Condition:
    """Parse a YAML ``when`` value into a :data:`Condition` tree.

    Accepts a string leaf, a list (implicit AND), or ``{all: [...]}`` /
    ``{any: [...]}`` (nestable).
    """
    if isinstance(cond, str):
        return _parse_leaf(cond)
    if isinstance(cond, (list, tuple)):
        if not cond:
            raise ValueError("when: condition list must not be empty")
        return AllOf(tuple(parse_condition(c) for c in cond))
    if isinstance(cond, dict):
        if len(cond) != 1:
            raise ValueError("when: dict must have exactly one of 'all'/'any'")
        (key, value), = cond.items()
        if key not in ("all", "any"):
            raise ValueError(f"when: unknown combinator {key!r}; use 'all' or 'any'")
        if isinstance(value, (list, tuple)):
            if not value:
                raise ValueError(f"when: {key!r} expects a non-empty list")
            children = tuple(parse_condition(c) for c in value)
        else:
            children = (parse_condition(value),)
        return (AllOf if key == "all" else AnyOf)(children)
    raise ValueError(
        f"when: expected a string, list, or dict; got {type(cond).__name__}")


def iter_condition_leaves(cond: Condition | None) -> Iterator[Leaf]:
    """Yield every :class:`Leaf` in ``cond`` (leaves only; no combinators)."""
    if cond is None:
        return
    if isinstance(cond, Leaf):
        yield cond
    else:
        for child in cond.conditions:
            yield from iter_condition_leaves(child)


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    params: dict[str, Any] = {}
    inputs: dict[str, str] = {}      # kwarg name -> "node.port" | "$input.key"
    when: Any = None                 # gate: str | list | dict (see parse_condition)

    @field_validator("when", mode="before")
    @classmethod
    def _parse_when(cls, value: Any) -> Any:
        if value is None or isinstance(value, (Leaf, AllOf, AnyOf)):
            return value
        return parse_condition(value)


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    max_workers: int = 4
    nodes: dict[str, NodeSpec]
    outputs: dict[str, str] = {}

    @model_validator(mode="after")
    def _check_refs(self):
        refs = [r for n in self.nodes.values() for r in n.inputs.values()]
        refs += [f"{leaf.node}.{leaf.port}" for n in self.nodes.values()
                 for leaf in iter_condition_leaves(n.when)]
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

"""Node base class. A node is a stateful object: setup() once, run() many times."""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class NodeParams(BaseModel):
    """Base for per-node config schemas. Unknown keys are errors (catches YAML typos)."""
    model_config = ConfigDict(extra="forbid")


class Node:
    type_name: ClassVar[str] = ""
    Params: ClassVar[type[NodeParams]] = NodeParams
    # Declared output ports; lets the pipeline validate wiring at load time.
    outputs: ClassVar[tuple[str, ...]] = ()

    def __init__(self, name: str, params: NodeParams):
        self.name = name
        self.params = params

    def setup(self) -> None:
        """Load models, warm up kernels, allocate GPU buffers. Called once."""

    def run(self, *args: Any, **inputs: Any) -> dict[str, Any]:
        """Per-request work. Must return a dict with exactly the declared `outputs`."""
        raise NotImplementedError

    def teardown(self) -> None:
        """Release resources."""
